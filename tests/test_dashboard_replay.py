"""Saved-run paging and browser controls on a reproducible ten-leecher recording."""
import json
import os
import shutil
import subprocess
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from dashboard.mock_events import generate
from dashboard.server import Dashboard, Handler, ROOT


def test_replay_pages_are_frozen_and_time_ordered(tmp_path):
    generate(tmp_path)
    app = Dashboard(tmp_path, replay=True)
    state = app.snapshot(0, "")
    assert state["replay"] and not state["demo"] and state["more"]
    assert state["protocol"] == "murmurtp"
    with (tmp_path / "events-harness.jsonl").open("a") as stream:
        print(json.dumps(dict(node="harness", type="marker", t=999)), file=stream)
    rows = state["events"].copy()
    while state["more"]:
        state = app.snapshot(state["cursor"], state["revision"])
        rows.extend(state["events"])
    assert len(rows) == state["cursor"]
    assert [r["t"] for r in rows] == sorted(r["t"] for r in rows)
    assert rows[-1]["t"] == 104
    assert sum(r["type"] == "summary" for r in rows) == 11
    assert Dashboard(tmp_path).snapshot(0, "")["replay"] is False


def test_replay_browser_seek_play_and_transport(tmp_path):
    if os.environ.get("MURMUR_TEST_CHROME") != "1":
        pytest.skip("set MURMUR_TEST_CHROME=1 for the headless browser check")
    chrome = (shutil.which("google-chrome") or shutil.which("chromium") or
              shutil.which("chrome") or str(Path(os.environ.get("ProgramFiles", "")) /
                                            "Google/Chrome/Application/chrome.exe"))
    if not Path(chrome).is_file():
        pytest.skip("headless Chrome unavailable")
    logs = tmp_path / "logs"
    generate(logs)
    # Exercise the real controls after the production JS has drained all pages.
    checks = """
    const check = (ok, msg) => { if (!ok) throw Error(msg); };
    const el = id => document.getElementById(id);
    function scrub(t) {
      el("timeline").value=t;
      el("timeline").dispatchEvent(new Event("input"));
    }
    async function review() {
      if (el("play").disabled) {setTimeout(review, 50);return;}
      try {
        check(el("finished").textContent==="0 / 10","start");
        check(el("transport-choice").hidden && el("recorded-transport").textContent.includes("MurmurTP"),"recorded protocol");
        scrub(104);
        check(el("finished").textContent==="10 / 10","end");
        check(el("play").textContent==="Replay","restart button");
        scrub(16);
        check(el("pieces").textContent==="128 / 256","rewind pieces");
        check(el("finished").textContent==="0 / 10","rewind completion");
        el("metric").value="overlay";el("metric").dispatchEvent(new Event("change"));
        check(el("chart").querySelectorAll("polyline").length===3,"overlay");
        check(!el("feed").textContent.includes("summary"),"future event");
        el("speed").value="4";el("play").click();
        await new Promise(resolve=>setTimeout(resolve, 650));
        el("play").click();
        check(Number(el("timeline").value)>17 && el("status").textContent==="Paused","play/pause");
        scrub(104);el("play").click();
        check(Number(el("timeline").value)===0,"restart");
        scrub(104);
        check(el("pieces").textContent==="256 / 256","final pieces");
        document.documentElement.dataset.review="passed";
      } catch (e) {document.documentElement.dataset.review=e.message;}
    }
    review();
    """
    class ReviewHandler(Handler):
        def do_GET(self):
            if self.path != "/app.js":
                return super().do_GET()
            content = (ROOT / "static/app.js").read_bytes() + checks.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    with ThreadingHTTPServer(("127.0.0.1", 0), ReviewHandler) as http:
        http.app = Dashboard(logs, replay=True)
        worker = Thread(target=http.serve_forever)
        worker.start()
        try:
            result = subprocess.run([chrome, "--headless", "--disable-gpu", "--disable-extensions",
                                     "--no-first-run", "--disable-background-networking",
                                     f"--user-data-dir={tmp_path / 'browser'}", "--dump-dom",
                                     "--virtual-time-budget=12000", f"http://127.0.0.1:{http.server_port}/"],
                                    capture_output=True, timeout=45, check=True)
        finally:
            http.shutdown()
            worker.join()
    assert b'data-review="passed"' in result.stdout, result.stdout[:500]


def test_replay_controls_with_recorded_pages(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("JavaScript runtime unavailable")
    generate(tmp_path)
    app = Dashboard(tmp_path, replay=True)
    pages = [app.snapshot(0, "")]
    while pages[-1]["more"]:
        pages.append(app.snapshot(pages[-1]["cursor"], pages[-1]["revision"]))
    # Drive the production event reducer and controls with a deterministic clock.
    prelude = """
    const assert=require("node:assert/strict");
    const pages=PAGES;
    const elements=new Map();
    function element(tag) {return {tag,style:{},options:[],children:[],setAttribute(){},
      append(...items){this.children.push(...items);},
      replaceChildren(...items){this.children=items;this.options=items;}};}
    const Option=class {constructor(text,value){this.text=text;this.value=value;}};
    const document={createElement:element,createElementNS:(_,tag)=>element(tag),
      createTextNode:text=>({textContent:text}),querySelectorAll:()=>[],getElementById(id) {
      if(!elements.has(id)) elements.set(id,element(id));
      return elements.get(id);
    }};
    let nextPage, tick, now=0;
    const performance={now:()=>now};
    const setTimeout=fn=>{nextPage=fn;};
    const setInterval=fn=>{tick=fn;return 1;};
    const clearInterval=()=>{tick=null;};
    let posted;
    const fetch=async(url,options={})=>{
      if(options.method==="POST") posted=url;
      return {ok:true,json:async()=>pages.shift()};
    };
    """.replace("PAGES", json.dumps(pages))
    app_js = (ROOT / "static/app.js").read_text(encoding="utf-8")
    controls = """
    (async()=>{
      await Promise.resolve(); await Promise.resolve();
      while(nextPage){const fn=nextPage;nextPage=null;await fn();}
      assert.equal(pages.length,0);
      const peer=()=>peers.get("events-10.0.1.11.jsonl");
      const scrub=t=>$("timeline").oninput({target:{value:t}});
      const finished=()=>[...peers.values()].filter(p=>!p.seed && p.complete).length;
      assert.equal(finished(),0);
      assert.equal($("recorded-transport").textContent,"Recorded transport · MurmurTP");
      assert.equal($("transport-choice").hidden,true);
      scrub(104); assert.equal(finished(),10); assert.equal($("play").textContent,"Replay");
      assert.equal(peer().pieces.size,256);
      scrub(16); assert.equal(peer().pieces.size,128); assert.equal(finished(),0);
      assert.equal($("play").textContent,"Play");
      assert.ok(!feed.some(e=>e.t>16));
      assert.ok(peer().links.get("1").every(e=>e.t<=16));
      assert.equal(peer().rates.at(-1).t,16);
      $("metric").value="overlay";$("link").value="1";draw(peer());
      assert.equal($("chart").children.filter(n=>n.tag==="polyline").length,3);
      assert.match($("scale").textContent,/KiB .*20 ms .*0 retx/);
      $("speed").value="4"; $("play").onclick(); now+=500; tick();
      assert.equal(playhead,18); $("play").onclick(); assert.equal(tick,null);
      scrub(104); $("play").onclick(); assert.equal(playhead,0);
      now+=30000;tick(); assert.equal(tick,null); assert.equal(finished(),10);
      assert.equal($("finished").textContent,"10 / 10");
      latest={...latest,demo:true,replay:false,status:"Ready"};
      $("tcp").checked=true; $("mtp").checked=false; $("tcp").onchange();
      assert.equal($("transport-choice").hidden,false);
      assert.equal($("tcp").disabled,false);
      assert.equal($("run").textContent,"Run TCP transfer");
      await $("run").onclick();
      assert.equal(posted,"/api/demo/tcp");
    })().catch(err=>{console.error(err);process.exitCode=1;});
    """
    subprocess.run([node, "-"], input=prelude + app_js + controls, text=True,
                   encoding="utf-8", check=True, timeout=20)
