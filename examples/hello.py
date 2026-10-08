from manim import *


class HelloScene(Scene):
    def construct(self):
        title = Text("Hello, Manim!", font_size=64)
        formula = MathTex(r"e^{i\pi} + 1 = 0", font_size=72).next_to(title, DOWN, buff=0.8)
        box = SurroundingRectangle(formula, color=YELLOW, buff=0.25)

        self.play(Write(title))
        self.play(FadeIn(formula, shift=UP))
        self.play(Create(box))
        self.wait(1)
