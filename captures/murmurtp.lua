-- MurmurTP v1: frozen 25-byte network-order header, UDP/6882.
-- Stateless packet inspection only; no inferred RTT, loss, or stream reassembly.
local proto = Proto("murmurtp", "Murmur Transport Protocol")
local f = {
    flags = ProtoField.uint8("murmurtp.flags", "Flags", base.HEX),
    conn_id = ProtoField.uint16("murmurtp.conn_id", "Connection ID", base.DEC),
    seq = ProtoField.uint32("murmurtp.seq", "Packet sequence number", base.DEC),
    ack = ProtoField.uint32("murmurtp.ack", "Next expected packet", base.DEC),
    window = ProtoField.uint16("murmurtp.window", "Receive window (packets)", base.DEC),
    sack = ProtoField.uint32("murmurtp.sack", "SACK bitmap", base.HEX),
    sack_seq = ProtoField.uint32("murmurtp.sack_seq", "Selectively acknowledged packet", base.DEC),
    timestamp = ProtoField.uint32("murmurtp.timestamp", "Sender clock (ms, low 32 bits)", base.DEC),
    length = ProtoField.uint16("murmurtp.length", "Payload length (bytes)", base.DEC),
    checksum = ProtoField.uint16("murmurtp.checksum", "Checksum", base.HEX),
    checksum_good = ProtoField.bool("murmurtp.checksum_good", "Checksum correct"),
    payload = ProtoField.bytes("murmurtp.payload", "Application payload")
}
local flag_names = {"syn", "ack", "fin", "data", "rst"}
for i, name in ipairs(flag_names) do
    f["flag_" .. name] = ProtoField.bool("murmurtp.flags." .. name, string.upper(name), 8, nil, 2 ^ (i - 1))
end
proto.fields = f
local e = {
    truncated = ProtoExpert.new("murmurtp.truncated", "Truncated MurmurTP capture/header", expert.group.MALFORMED, expert.severity.ERROR),
    length = ProtoExpert.new("murmurtp.bad_length", "Invalid MurmurTP payload length", expert.group.MALFORMED, expert.severity.ERROR),
    checksum = ProtoExpert.new("murmurtp.bad_checksum", "MurmurTP checksum mismatch", expert.group.CHECKSUM, expert.severity.WARN),
    flags = ProtoExpert.new("murmurtp.bad_flags", "Unknown or unexpected flag combination", expert.group.PROTOCOL, expert.severity.WARN),
    control = ProtoExpert.new("murmurtp.control_payload", "Payload on a control packet", expert.group.PROTOCOL, expert.severity.WARN),
    sack = ProtoExpert.new("murmurtp.bad_sack", "SACK sequence exceeds v1 sequence space", expert.group.PROTOCOL, expert.severity.WARN)
}
proto.experts = e

local function checksum(tvb)
    -- The checksum field starts at odd offset 23: zero both bytes and compare
    -- the numeric stored value, rather than checking checksum(datagram) == 0.
    local function byte(offset)
        if offset >= tvb:len() or offset == 23 or offset == 24 then return 0 end
        return tvb(offset, 1):uint()
    end
    local total = 0
    for offset = 0, tvb:len() - 1, 2 do
        total = total + byte(offset) * 256 + byte(offset + 1)
    end
    while total >= 65536 do total = total % 65536 + math.floor(total / 65536) end
    return 65535 - total
end

function proto.dissector(tvb, pinfo, tree)
    pinfo.cols.protocol:set("MurmurTP")
    local root = tree:add(proto, tvb())
    local size = tvb:len()
    if size < 25 then
        pinfo.cols.info:set("Truncated MurmurTP header")
        root:add_proto_expert_info(e.truncated)
        return size
    end
    local flags, ack, sack = tvb(0, 1):uint(), tvb(7, 4):uint(), tvb(13, 4):uint()
    local flag_tree = root:add(f.flags, tvb(0, 1))
    local labels = {}
    for i, name in ipairs(flag_names) do
        flag_tree:add(f["flag_" .. name], tvb(0, 1))
        if math.floor(flags / 2 ^ (i - 1)) % 2 == 1 then labels[#labels + 1] = string.upper(name) end
    end
    local legal = {[1]=true, [2]=true, [3]=true, [4]=true, [8]=true, [16]=true}
    if not legal[flags] then flag_tree:add_proto_expert_info(e.flags) end
    root:add(f.conn_id, tvb(1, 2))
    root:add(f.seq, tvb(3, 4))
    root:add(f.ack, tvb(7, 4))
    root:add(f.window, tvb(11, 2))
    local sack_tree = root:add(f.sack, tvb(13, 4))
    for bit = 0, 31 do
        if math.floor(sack / 2 ^ bit) % 2 == 1 then
            local seq = ack + 1 + bit
            if seq <= 4294967295 then
                sack_tree:add(f.sack_seq, seq):set_generated()
            else sack_tree:add_proto_expert_info(e.sack) end
        end
    end
    root:add(f.timestamp, tvb(17, 4))
    local length = tvb(21, 2):uint()
    root:add(f.length, tvb(21, 2))
    root:add(f.checksum, tvb(23, 2))
    pinfo.cols.info:set(string.format("%s Conn=%d Seq=%d Ack=%d Win=%d Len=%d",
        #labels > 0 and table.concat(labels, "|") or "NONE", tvb(1, 2):uint(),
        tvb(3, 4):uint(), ack, tvb(11, 2):uint(), length))
    if size < tvb:reported_len() then
        root:add_proto_expert_info(e.truncated)
    elseif length > 1200 or size ~= 25 + length then
        root:add_proto_expert_info(e.length)
    else
        local good = checksum(tvb) == tvb(23, 2):uint()
        root:add(f.checksum_good, good):set_generated()
        if not good then root:add_proto_expert_info(e.checksum) end
    end
    if flags ~= 8 and length > 0 then root:add_proto_expert_info(e.control) end
    if size > 25 then root:add(f.payload, tvb(25, size - 25)) end
    return size
end

local udp = DissectorTable.get("udp.port")
udp:add(6882, proto)
udp:add_for_decode_as(proto)
