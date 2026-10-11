extends Node2D
## Original, code-drawn scenery. No downloaded character or game artwork.

const INK = Color("061019")
const MOSS = Color("42665c")
var elapsed := 0.0
var motes: Array[Vector3] = []

func _ready() -> void:
	# A visual-only deterministic pattern; it never chooses combat behavior.
	for i in range(44):
		motes.append(Vector3(fposmod(i * 137.17, 1240) + 20, fposmod(i * 97.3, 400) + 110, float(i % 7)))

func _process(delta: float) -> void:
	elapsed += delta
	queue_redraw()

func _draw() -> void:
	draw_rect(Rect2(-30, -30, 1340, 780), Color("08151e"))
	# Moonlight, broken archways and layered tree silhouettes.
	draw_circle(Vector2(820, 165), 116, Color(0.22, 0.34, 0.34, 0.035))
	draw_circle(Vector2(820, 165), 81, Color(0.38, 0.56, 0.51, 0.04))
	draw_circle(Vector2(820, 165), 47, Color("8eaaa0"))
	draw_circle(Vector2(838, 151), 46, Color("10202a"))
	for i in range(10):
		var x := float(i * 155 - 30)
		var lean := float((i % 3) * 18 - 18)
		draw_colored_polygon(PackedVector2Array([Vector2(x, 500), Vector2(x + 19, 180), Vector2(x + 42 + lean, -40), Vector2(x + 75 + lean, -40), Vector2(x + 49, 510)]), Color("0d2028"))
		for j in range(3):
			draw_line(Vector2(x + 35, 200 + j * 65), Vector2(x - 55 + lean, 60 + j * 65), Color("0d2028"), 14 - j * 3)
	for x in [190.0, 520.0, 1050.0]:
		draw_rect(Rect2(x, 230, 26, 320), Color("182c33"))
		draw_rect(Rect2(x + 128, 270, 22, 280), Color("182c33"))
		draw_arc(Vector2(x + 75, 258), 66, PI, TAU, 30, Color("182c33"), 27, true)
		draw_arc(Vector2(x + 75, 258), 46, PI, TAU, 24, Color("263c3c"), 2, true)
		draw_line(Vector2(x + 6, 220), Vector2(x + 20, 450), Color("37534a"), 3)
	# Gates give clear, readable arena bounds.
	for x in [72.0, 1208.0]:
		draw_rect(Rect2(x - 25, 350, 50, 237), Color("14292d"))
		draw_rect(Rect2(x - 37, 337, 74, 18), Color("3c554d"))
		for y in range(379, 560, 42):
			draw_line(Vector2(x - 20, y), Vector2(x + 20, y), Color("273b3b"), 2)
	# Mist and the playable floor.
	for i in range(5):
		draw_rect(Rect2(0, 490 + i * 18, 1280, 22), Color(0.2, 0.36, 0.32, 0.012 + i * 0.004))
	draw_rect(Rect2(-30, 570, 1340, 200), Color("101f24"))
	draw_rect(Rect2(-30, 570, 1340, 9), Color("617566"))
	draw_line(Vector2(0, 570), Vector2(1280, 570), Color("a1ac86"), 2)
	for i in range(36):
		var x := i * 39.0
		draw_line(Vector2(x, 579), Vector2(x + 12, 637), Color("1a3030"), 2)
		draw_line(Vector2(x, 647), Vector2(x + 35, 645), Color("243c37"), 1)
		var grass := 6.0 + float(i % 4) * 3.0
		draw_line(Vector2(x + 9, 570), Vector2(x + 4, 570 - grass), MOSS, 2)
		draw_line(Vector2(x + 12, 570), Vector2(x + 18, 570 - grass * 0.7), MOSS, 2)
	for mote in motes:
		var p := Vector2(mote.x + sin(elapsed * 0.3 + mote.z) * 15, mote.y + sin(elapsed * 0.6 + mote.x) * 13)
		var alpha := 0.2 + (sin(elapsed + mote.z) + 1) * 0.15
		draw_circle(p, 1.4 if int(mote.z) % 2 else 2.0, Color(0.68, 0.86, 0.65, alpha))
