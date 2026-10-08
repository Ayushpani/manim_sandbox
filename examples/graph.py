from manim import *


class SineWave(Scene):
    def construct(self):
        axes = Axes(x_range=[-PI, PI, PI / 2], y_range=[-1.5, 1.5, 0.5], x_length=10, y_length=5)
        labels = axes.get_axis_labels(x_label="x", y_label="y")
        curve = axes.plot(np.sin, color=BLUE)
        label = MathTex(r"y = \sin x", color=BLUE).to_corner(UR)

        dot = Dot(color=YELLOW).move_to(axes.c2p(-PI, 0))
        t = ValueTracker(-PI)
        dot.add_updater(lambda d: d.move_to(axes.c2p(t.get_value(), np.sin(t.get_value()))))

        self.play(Create(axes), Write(labels))
        self.play(Create(curve), Write(label), run_time=2)
        self.add(dot)
        self.play(t.animate.set_value(PI), run_time=3, rate_func=linear)
        self.wait()
