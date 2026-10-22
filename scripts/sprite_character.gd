extends Node2D
## Full-canvas character. Node origin is its stage foot position.
## Attach to Node2D; supply an animations.json produced by prepare_animation.py.

@export_file("*.json") var manifest_path := ""
@export var idle_action: StringName = &"idle"
@export var autoplay := true
@export var keyboard_test := false

var sprite: AnimatedSprite2D
var action_specs: Dictionary = {}
var loaded_actions: Dictionary = {}
var frames := SpriteFrames.new()
var animations_root := ""
var fps := 16.0
var current_action: StringName = &""


func _ready() -> void:
	sprite = AnimatedSprite2D.new()
	sprite.centered = false
	add_child(sprite)
	frames.remove_animation(&"default")
	sprite.sprite_frames = frames
	sprite.animation_finished.connect(_on_animation_finished)
	if not load_manifest():
		return
	if autoplay:
		if action_specs.has(idle_action):
			play_action(idle_action)
		elif not action_specs.is_empty():
			play_action(action_specs.keys()[0])


func load_manifest() -> bool:
	if not FileAccess.file_exists(manifest_path):
		push_error("Missing manifest: " + manifest_path)
		return false
	var data = JSON.parse_string(FileAccess.get_file_as_string(manifest_path))
	if not data is Dictionary or not data.get("animations") is Array:
		push_error("Invalid animation manifest")
		return false
	fps = float(data.get("fps", 16))
	if fps <= 0:
		return false
	animations_root = manifest_path.get_base_dir()
	for entry in data.animations:
		if not entry is Dictionary:
			return false
		var action := StringName(str(entry.get("name", "")))
		var directory := str(entry.get("directory", ""))
		var mode := str(entry.get("playback", "hold"))
		if action.is_empty() or action_specs.has(action) or int(entry.get("frame_count", 0)) <= 0:
			return false
		if directory.is_empty() or directory in [".", ".."] or "/" in directory or "\\" in directory:
			return false
		if mode not in ["loop", "return_idle", "hold"]:
			return false
		action_specs[action] = entry
		frames.add_animation(action)
		frames.set_animation_speed(action, fps)
		frames.set_animation_loop(action, mode == "loop")
	for entry in action_specs.values():
		if entry.playback == "return_idle":
			if not action_specs.has(idle_action) or action_specs[idle_action].playback != "loop":
				push_error("return_idle requires a looping idle_action")
				return false
	return not action_specs.is_empty()


func ensure_action_loaded(action: StringName) -> bool:
	if not action_specs.has(action):
		return false
	if loaded_actions.has(action):
		return true
	var spec: Dictionary = action_specs[action]
	for index in range(1, int(spec.frame_count) + 1):
		var path := animations_root.path_join(str(spec.directory)).path_join("frame_%06d.png" % index)
		var image := Image.load_from_file(path)
		if image == null or image.is_empty():
			frames.clear(action)
			push_error("Cannot decode " + path)
			return false
		if image.get_width() != int(spec.width) or image.get_height() != int(spec.height):
			frames.clear(action)
			return false
		frames.add_frame(action, ImageTexture.create_from_image(image))
	loaded_actions[action] = true
	return true


func play_action(action: StringName, restart := false) -> bool:
	if not ensure_action_loaded(action):
		return false
	if current_action == action and not restart:
		return true
	var layout: Dictionary = action_specs[action].get("layout", {})
	var factor := float(layout.get("scale", 1.0))
	var spec: Dictionary = action_specs[action]
	var anchor: Array = layout.get("anchor", [float(spec.width) / 2, float(spec.height)])
	if factor <= 0 or anchor.size() != 2:
		return false
	sprite.scale = Vector2.ONE * factor
	sprite.position = -Vector2(float(anchor[0]), float(anchor[1])) * factor
	sprite.stop()
	sprite.play(action)
	current_action = action
	return true


func _on_animation_finished() -> void:
	var mode: String = action_specs[sprite.animation].playback
	if mode == "return_idle":
		play_action(idle_action, true)
	elif mode == "hold":
		sprite.pause()


func _unhandled_key_input(event: InputEvent) -> void:
	if not keyboard_test or not event is InputEventKey or not event.pressed or event.echo:
		return
	var index: int = event.keycode - KEY_1
	if index >= 0 and index < mini(9, action_specs.size()):
		play_action(action_specs.keys()[index], true)
