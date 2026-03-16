from PIL import Image, ImageDraw, ImageFont

SIZE = 1024
OUT = "midi_fighter_twistluhh_icon.png"

img = Image.new("RGBA", (SIZE, SIZE), (18, 20, 26, 255))
d = ImageDraw.Draw(img)

margin = 40
d.rounded_rectangle(
    (margin, margin, SIZE - margin, SIZE - margin),
    radius=140,
    fill=(28, 31, 40, 255),
    outline=(85, 92, 112, 255),
    width=10,
)

d.rounded_rectangle((130, 95, SIZE - 130, 180), radius=24, fill=(16, 18, 24, 255))

try:
    font_main = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 42)
    font_footer = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 34)
except Exception:
    font_main = ImageFont.load_default()
    font_footer = ImageFont.load_default()

d.text((165, 115), "MIDI FIGHTER TWISTER", fill=(230, 233, 240, 255), font=font_main)

grid_margin_x = 140
grid_margin_y = 230
cell = 186
knob_size = 120
colors = [
    (255, 94, 94, 255),
    (255, 187, 85, 255),
    (255, 227, 92, 255),
    (105, 220, 120, 255),
    (92, 207, 255, 255),
    (130, 155, 255, 255),
    (181, 123, 255, 255),
    (255, 132, 206, 255),
]

for r in range(4):
    for c in range(4):
        x0 = grid_margin_x + c * cell
        y0 = grid_margin_y + r * cell
        x1 = x0 + knob_size
        y1 = y0 + knob_size
        color = colors[(r * 4 + c) % len(colors)]

        d.ellipse((x0 - 8, y0 - 8, x1 + 8, y1 + 8), fill=(10, 11, 14, 255))
        d.ellipse((x0, y0, x1, y1), fill=(37, 40, 52, 255), outline=(96, 102, 122, 255), width=6)
        d.arc((x0 + 14, y0 + 14, x1 - 14, y1 - 14), start=210, end=330, fill=color, width=10)
        d.ellipse((x0 + 34, y0 + 34, x1 - 34, y1 - 34), fill=(18, 20, 27, 255), outline=(58, 63, 80, 255), width=3)

for i in range(3):
    y = 300 + i * 130
    d.rounded_rectangle((45, y, 95, y + 90), radius=14, fill=(50, 55, 70, 255), outline=(96, 102, 122, 255), width=3)
    d.rounded_rectangle((SIZE - 95, y, SIZE - 45, y + 90), radius=14, fill=(50, 55, 70, 255), outline=(96, 102, 122, 255), width=3)

d.rounded_rectangle((170, SIZE - 150, SIZE - 170, SIZE - 90), radius=16, fill=(16, 18, 24, 255))
d.text((212, SIZE - 132), "TWISTLUHH UTILITY", fill=(214, 220, 236, 255), font=font_footer)

img.save(OUT)
print(f"Wrote {OUT}")
