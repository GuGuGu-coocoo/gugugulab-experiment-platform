extends Node2D
## Small deterministic visual bursts. These never alter game or event values.
var particles: Array[Dictionary] = []
var texts: Array[Dictionary] = []

func burst(point: Vector2, color: Color, amount := 12, strength := 260.0) -> void:
	for i in range(amount):
		var angle := float(i) / amount * TAU
		particles.append({"p": point, "v": Vector2(cos(angle), sin(angle)) * strength * (0.45 + float(i % 4) * 0.13), "life": 0.45, "max": 0.45, "color": color})

func floating_text(point: Vector2, text: String, color := Color("eac47c")) -> void:
	texts.append({"p": point, "text": text, "life": 0.85, "color": color})

func _process(delta: float) -> void:
	for p in particles:
		p.p += p.v * delta
		p.v *= maxf(0.0, 1.0 - delta * 3.0)
		p.life -= delta
	particles = particles.filter(func(p): return p.life > 0)
	for t in texts:
		t.p.y -= delta * 32
		t.life -= delta
	texts = texts.filter(func(t): return t.life > 0)
	queue_redraw()

func _draw() -> void:
	for p in particles:
		var c: Color = p.color
		c.a = p.life / p.max
		draw_line(p.p, p.p - p.v.normalized() * 7, c, 2, true)
	var font := ThemeDB.fallback_font
	for t in texts:
		var c: Color = t.color
		c.a = minf(1.0, t.life * 3)
		draw_string(font, t.p, t.text, HORIZONTAL_ALIGNMENT_CENTER, -1, 18, c)
