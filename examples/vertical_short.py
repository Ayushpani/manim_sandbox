from manim import *

# Made for the "Vertical" toggle (9:16). The frame is 8 units wide and about
# 14.2 tall, so stack things top to bottom instead of left to right.


class PythagorasShort(Scene):
    def construct(self):
        title = Text("Pythagoras", font_size=56).to_edge(UP, buff=1.2)

        a, b = 3, 4
        tri = Polygon(ORIGIN, RIGHT * a, RIGHT * a + UP * b, color=WHITE)
        tri.scale(0.6).move_to(UP * 1.5)

        eq = MathTex("a^2", "+", "b^2", "=", "c^2", font_size=80).next_to(tri, DOWN, buff=1)
        eq[0].set_color(BLUE)
        eq[2].set_color(GREEN)
        eq[4].set_color(YELLOW)
        nums = MathTex("9", "+", "16", "=", "25", font_size=80).next_to(eq, DOWN, buff=0.8)

        self.play(Write(title))
        self.play(Create(tri))
        self.play(Write(eq))
        self.play(TransformFromCopy(eq, nums))
        self.play(Indicate(nums[4]))
        self.wait()
