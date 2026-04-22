"""
Config patch note:
slow_duration_seconds needs to be added to DemoConfig.
This is the total duration of the slow phase (burst + drain).
burst_duration_seconds is the overload window within that.
Default: 180s total (90s burst + 90s drain).
"""
