from pathlib import Path

from PIL import Image, ImageDraw


root = Path(__file__).resolve().parents[1]
canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
draw = ImageDraw.Draw(canvas)

draw.rounded_rectangle((8, 8, 248, 248), radius=68, fill="#111827")

white = "#F8FAFC"
mint = "#4ED5C3"
stroke = 15
draw.line((91, 53, 165, 53), fill=white, width=stroke)
draw.line((108, 53, 108, 112), fill=white, width=stroke)
draw.line((148, 53, 148, 112), fill=white, width=stroke)
draw.line((108, 108, 55, 197), fill=white, width=stroke)
draw.line((148, 108, 201, 197), fill=white, width=stroke)
draw.arc((50, 178, 206, 226), 0, 180, fill=white, width=stroke)
draw.line((55, 202, 201, 202), fill=mint, width=18)
draw.ellipse((91, 154, 109, 172), fill=mint)
draw.ellipse((135, 136, 153, 154), fill=mint)
draw.ellipse((158, 166, 176, 184), fill=mint)

output = root / "public" / "ampgent.ico"
canvas.save(output, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(output)
