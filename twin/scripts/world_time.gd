extends RefCounted

## One shared definition of how fast city time runs, used by the environment
## clock, the society kernel and anything else that reads "what time is it".
##
## Simulated time (SUMO's clock) is authoritative: traffic, citizens, crews and
## incidents all advance with it, however many times faster than real time the
## operator runs it. The city clock is derived from it by a fixed ratio, the
## clock rate: how many clock seconds pass per simulated second.
##
##   1× (real time)  one simulated second is one clock second, so a car that
##                   needs seven seconds to cross a block takes seven clock
##                   seconds, and a three-minute incident reads as three minutes.
##   24×             a whole day lasts one hour of simulated time.
##   120×            a whole day lasts twelve minutes (the old fixed pace).
##
## Everything that is measured in clock time is scaled by the same ratio, so the
## clock never runs ahead of or behind what is happening on screen.

const REAL_TIME := 1.0
const DEFAULT_RATE := REAL_TIME
## [label, clock seconds per simulated second]
const PRESETS := [["Real time", 1.0], ["Day = 1 h", 24.0], ["Day = 12 min", 120.0]]

## Clock seconds per simulated second.
static var clock_rate := DEFAULT_RATE
## True while SUMO is paused: the clock and the society stand still with it.
static var paused := false


static func minutes_per_second() -> float:
	## Clock minutes that pass per simulated second.
	return clock_rate / 60.0


static func advance_minutes(delta_seconds: float) -> float:
	## Clock minutes to add for delta_seconds of simulated time (none when paused).
	return 0.0 if paused else delta_seconds * minutes_per_second()


static func reset() -> void:
	clock_rate = DEFAULT_RATE
	paused = false


static func format_clock(clock_minute: float, with_seconds: bool) -> String:
	## "HH:MM" or "HH:MM:SS" for a minute count (may include whole days).
	var total_seconds := int(floor(fposmod(clock_minute, 1440.0) * 60.0))
	var hours := int(total_seconds / 3600)
	var minutes := int(total_seconds / 60) % 60
	if with_seconds:
		return "%02d:%02d:%02d" % [hours, minutes, total_seconds % 60]
	return "%02d:%02d" % [hours, minutes]
