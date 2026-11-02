"""画大肥鱼表情包和头像：python stickers/make_stickers.py
Q版蓝色胖鱼，头上扣一个不锈钢盆。"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
S = 4                      # 超采样倍数，画完缩小得到抗锯齿
W = 512
FONT = "C:/Windows/Fonts/msyhbd.ttc"

BLUE = (92, 168, 245)
BLUE_DARK = (58, 124, 206)
BELLY = (196, 230, 255)
LINE = (34, 70, 128)
PINK = (255, 140, 170, 150)
STEEL_LIGHT = (232, 236, 242)
STEEL = (180, 188, 200)
STEEL_DARK = (122, 132, 148)


def s(*v):
    return [x * S for x in v]


def ell(d, cx, cy, rx, ry, fill=None, outline=None, width=0):
    d.ellipse(s(cx - rx, cy - ry, cx + rx, cy + ry), fill=fill, outline=outline, width=width * S)


def basin_layer():
    """不锈钢盆（倒扣），单独画在一层上方便歪着放。"""
    layer = Image.new("RGBA", (W * S, W * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx, cy = 236, 132
    # 盆身：上半个椭圆，竖条渐变做出金属感
    for i in range(-104, 105):
        t = (i + 104) / 208
        shade = 0.55 + 0.45 * math.cos((t - 0.35) * math.pi * 1.6)
        col = tuple(int(STEEL_DARK[k] + (STEEL_LIGHT[k] - STEEL_DARK[k]) * max(0, min(1, shade))) for k in range(3))
        h = 82 * math.sqrt(max(0, 1 - (i / 105) ** 2))
        d.line(s(cx + i, cy, cx + i, cy - h), fill=col, width=S + 1)
    d.arc(s(cx - 105, cy - 82, cx + 105, cy + 82), 180, 360, fill=LINE, width=5 * S)
    # 高光
    d.arc(s(cx - 70, cy - 62, cx + 10, cy + 40), 200, 260, fill=(255, 255, 255), width=7 * S)
    # 盆沿
    ell(d, cx, cy, 124, 20, fill=STEEL, outline=LINE, width=5)
    d.arc(s(cx - 110, cy - 12, cx + 110, cy + 12), 190, 350, fill=STEEL_LIGHT, width=4 * S)
    return layer.rotate(-9, center=(cx * S, cy * S), resample=Image.BICUBIC)


def fish(expr: str) -> Image.Image:
    img = Image.new("RGBA", (W * S, W * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 尾巴
    d.polygon(s(380, 255, 488, 168, 462, 255, 488, 342), fill=BLUE_DARK, outline=LINE, width=5 * S)
    # 身体 + 肚皮
    ell(d, 236, 262, 175, 145, fill=BLUE, outline=LINE, width=6)
    ell(d, 226, 318, 125, 80, fill=BELLY)
    # 背鳍（大部分被盆挡住）、侧鳍
    ell(d, 300, 358, 30, 16, fill=BLUE_DARK, outline=LINE, width=4)
    # 腮红
    blush = Image.new("RGBA", img.size, (0, 0, 0, 0))
    bd = ImageDraw.Draw(blush)
    ell(bd, 128, 292, 30, 16, fill=PINK)
    ell(bd, 318, 292, 30, 16, fill=PINK)
    img.alpha_composite(blush)
    d = ImageDraw.Draw(img)
    EXPRESSIONS[expr](d)
    img.alpha_composite(basin_layer())
    return img


# ---------- 五官 ----------
def eyes_round(d, big=False):
    r = 34 if big else 28
    for x in (172, 276):
        ell(d, x, 248, r, r + 6, fill=(30, 30, 40))
        ell(d, x - 9, 236, 11, 12, fill=(255, 255, 255))
        ell(d, x + 10, 262, 5, 5, fill=(255, 255, 255))


def eyes_happy(d):
    for x in (172, 276):
        d.arc(s(x - 26, 236, x + 26, 278), 200, 340, fill=(30, 30, 40), width=8 * S)


def eyes_closed(d):
    for x in (172, 276):
        d.arc(s(x - 26, 222, x + 26, 262), 20, 160, fill=(30, 30, 40), width=8 * S)


def heart(d, cx, cy, r, fill=(255, 80, 120)):
    d.ellipse(s(cx - r, cy - r, cx, cy), fill=fill)
    d.ellipse(s(cx, cy - r, cx + r, cy), fill=fill)
    d.polygon(s(cx - r, cy - r / 2 + 2, cx + r, cy - r / 2 + 2, cx, cy + r), fill=fill)


def mouth_smile(d, open_=False):
    if open_:
        d.chord(s(206, 278, 246, 318), 0, 180, fill=(200, 60, 80), outline=LINE, width=4 * S)
    else:
        d.arc(s(204, 280, 226, 300), 0, 180, fill=LINE, width=5 * S)
        d.arc(s(226, 280, 248, 300), 0, 180, fill=LINE, width=5 * S)


def e_love(d):
    for x in (172, 276):
        heart(d, x, 250, 30)
    mouth_smile(d, open_=True)
    heart(d, 70, 120, 20)
    heart(d, 420, 100, 26)
    heart(d, 455, 175, 14)


def e_happy(d):
    eyes_happy(d)
    mouth_smile(d, open_=True)


def e_cry(d):
    eyes_closed(d)
    for x in (160, 288):
        d.polygon(s(x, 262, x - 14, 300, x + 14, 300), fill=(120, 200, 255))
        ell(d, x, 300, 14, 14, fill=(120, 200, 255))
    d.arc(s(210, 292, 244, 314), 180, 360, fill=LINE, width=5 * S)


def e_shock(d):
    eyes_round(d, big=True)
    ell(d, 226, 306, 14, 18, fill=(120, 30, 50), outline=LINE, width=4)
    for i, x in enumerate((60, 84)):
        d.line(s(x, 150 + i * 6, x + 14, 200 + i * 6), fill=LINE, width=6 * S)


def e_sleep(d):
    eyes_closed(d)
    ell(d, 226, 300, 8, 6, fill=LINE)
    font = ImageFont.truetype(FONT, 46 * S)
    for i, (x, y) in enumerate(((400, 150), (432, 100), (462, 52))):
        d.text(s(x, y), "Z", font=ImageFont.truetype(FONT, (30 + i * 10) * S), fill=LINE)


def e_hungry(d):
    eyes_round(d)
    d.chord(s(198, 280, 254, 330), 0, 180, fill=(200, 60, 80), outline=LINE, width=4 * S)
    ell(d, 252, 322, 7, 11, fill=(140, 210, 255))  # 口水


def e_cheer(d):
    eyes_happy(d)
    mouth_smile(d, open_=True)
    for x, y, r in ((70, 140, 18), (440, 110, 22), (60, 330, 12)):
        d.polygon(s(x, y - r, x + r / 3, y - r / 3, x + r, y, x + r / 3, y + r / 3,
                    x, y + r, x - r / 3, y + r / 3, x - r, y, x - r / 3, y - r / 3), fill=(255, 205, 60))


def e_ok(d):
    eyes_round(d)
    mouth_smile(d)


def e_confused(d):
    eyes_round(d)
    d.line(s(212, 300, 240, 296), fill=LINE, width=5 * S)
    d.text(s(410, 70), "?", font=ImageFont.truetype(FONT, 90 * S), fill=LINE)


def e_angry(d):
    eyes_round(d)
    for x, dx in ((172, 1), (276, -1)):
        d.line(s(x - 30 * dx, 200, x + 22 * dx, 220), fill=LINE, width=8 * S)
    d.arc(s(206, 296, 246, 318), 180, 360, fill=LINE, width=5 * S)
    ell(d, 412, 150, 26, 26, fill=(255, 90, 90))
    d.text(s(398, 124), "#", font=ImageFont.truetype(FONT, 40 * S), fill=(255, 255, 255))


EXPRESSIONS = {
    "love": e_love, "happy": e_happy, "cry": e_cry, "shock": e_shock, "sleep": e_sleep,
    "hungry": e_hungry, "cheer": e_cheer, "ok": e_ok, "confused": e_confused, "angry": e_angry,
}

CAPTIONS = {
    "love": "贴贴主人~", "happy": "嘿嘿嘿", "cry": "呜呜呜", "shock": "鱼鱼震惊", "sleep": "睡觉觉",
    "hungry": "饿饿 饭饭", "cheer": "冲鸭！", "ok": "收到！", "confused": "鱼鱼不懂", "angry": "哼！",
}


def caption(img: Image.Image, text: str):
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT, 62 * S)
    w = d.textlength(text, font=font)
    d.text(((W * S - w) / 2, 418 * S), text, font=font, fill=(40, 40, 50),
           stroke_width=7 * S, stroke_fill=(255, 255, 255))


def main():
    for name in EXPRESSIONS:
        img = fish(name)
        caption(img, CAPTIONS[name])
        img.resize((W, W), Image.LANCZOS).save(OUT / f"{name}.png")

    # 头像：浅蓝底，开心脸，鱼放大居中
    bg = Image.new("RGBA", (W * S, W * S), (214, 238, 255, 255))
    f = fish("happy")
    f = f.resize((int(W * S * 1.08), int(W * S * 1.08)), Image.LANCZOS)
    bg.alpha_composite(f, (-int(W * S * 0.06), int(W * S * 0.06)))
    bg.convert("RGB").resize((640, 640), Image.LANCZOS).save(OUT / "avatar.png")
    print("ok:", ", ".join(EXPRESSIONS), "+ avatar")


if __name__ == "__main__":
    main()
