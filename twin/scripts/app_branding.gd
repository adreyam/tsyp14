extends Node

## Sets the application's window icon (the TSYP14 square logo). The engine applies its own icon during startup, so the logo is
## set once the first frames are up and again a little later to be sure it sticks. On desktops that take a window's own icon
## (X11 / XWayland) this is what the dock and task switcher show.

const ICON_PATH := "res://assets/branding/lg-out.png"

func _ready() -> void:
	if DisplayServer.get_name().to_lower() == "headless":
		return
	for delay in [0.4, 1.5, 4.0]:
		await get_tree().create_timer(float(delay), true, false, true).timeout
		_apply()

func _apply() -> void:
	var image := Image.load_from_file(ProjectSettings.globalize_path(ICON_PATH))
	if image != null and not image.is_empty():
		image.convert(Image.FORMAT_RGBA8)
		image.resize(256, 256, Image.INTERPOLATE_LANCZOS)
		DisplayServer.set_icon(image)
