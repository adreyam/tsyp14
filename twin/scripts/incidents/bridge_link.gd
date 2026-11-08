extends RefCounted

## Operator/presenter commands to the SUMO bridge over the existing localhost
## control port. Each request carries an id; the bridge applies it once and
## republishes the acknowledgement for a few seconds, and this link resends
## until it sees that ack, so a dropped datagram never loses a command.

const RESEND_SECONDS := 0.5
const MAX_ATTEMPTS := 24

var _udp := PacketPeerUDP.new()
var _pending: Dictionary = {}
var _results: Dictionary = {}
var _counter := 0
var _opened := false
var _prefix := ""


func open() -> void:
	var port_text := OS.get_environment("CITY_SUMO_CONTROL_PORT")
	_udp.connect_to_host("127.0.0.1", int(port_text) if port_text.is_valid_int() else 8815)
	_opened = true
	_prefix = "g%d" % (Time.get_ticks_usec() % 100000)


func send(payload: Dictionary) -> String:
	if not _opened:
		open()
	_counter += 1
	var request := "%s-%d" % [_prefix, _counter]
	var message := payload.duplicate(true)
	message["command"] = "incident"
	message["request"] = request
	_pending[request] = {"message": message, "sent_msec": 0, "attempts": 0}
	_transmit(request)
	return request


func send_unreliable(payload: Dictionary) -> void:
	if not _opened:
		open()
	var message := payload.duplicate(true)
	message["command"] = "incident"
	_udp.put_packet(JSON.stringify(message).to_utf8_buffer())


func _transmit(request: String) -> void:
	var entry := _pending[request] as Dictionary
	entry.sent_msec = Time.get_ticks_msec()
	entry.attempts = int(entry.attempts) + 1
	_udp.put_packet(JSON.stringify(entry.message).to_utf8_buffer())


func process(state: Dictionary) -> Array[Dictionary]:
	## Consumes acks from the latest packet, resends unacknowledged requests and
	## returns the newly completed [{request, result}].
	var completed: Array[Dictionary] = []
	for ack_value in state.get("acks", []):
		if not ack_value is Dictionary:
			continue
		var request := str((ack_value as Dictionary).get("request", ""))
		if _pending.has(request):
			_pending.erase(request)
			_results[request] = str((ack_value as Dictionary).get("result", ""))
			completed.append({"request": request, "result": _results[request]})
	var now := Time.get_ticks_msec()
	for request in _pending.keys():
		var entry := _pending[request] as Dictionary
		if int(entry.attempts) >= MAX_ATTEMPTS:
			_pending.erase(request)
			_results[request] = "no acknowledgement from bridge"
			completed.append({"request": request, "result": _results[request]})
		elif now - int(entry.sent_msec) >= int(RESEND_SECONDS * 1000.0):
			_transmit(str(request))
	return completed


func result_of(request: String) -> String:
	return str(_results.get(request, ""))


func is_pending(request: String) -> bool:
	return _pending.has(request)
