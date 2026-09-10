"""Run the single-scooter design review: python -m smith_agents.scooter_preview."""
import json
import os
from pathlib import Path
import tempfile
import time


def main():
    with tempfile.TemporaryDirectory(prefix='widget-scooter-preview-') as directory:
        os.environ['SMITH_AGENTS_CONFIG_DIR'] = directory
        Path(directory, 'config.json').write_text(json.dumps({
            'dock': 'top-left', 'zoom': 1.0, 'theme': 'claude',
            'console_open': True, 'console_tab': 'agents',
            'visible': True, 'tucked': False,
        }))
        from . import core, scooter_motion
        core.APP_NAME = 'Scooter motion preview'
        from . import app
        app.AGENT_FRAME_MS = round(1000/scooter_motion.FPS)
        row = next(row for row in core.demo_agents(all_figures=True)
                   if core.agent_style(row)[0] == 'approved_35')
        row.update(name='Scooter · click to replay',
                   tail=[('cmd', 'Two pushes, then a quiet idle')])

        class Preview(app.SmithAgentsWidget):
            def __init__(self):
                self.started = time.monotonic()
                super().__init__(demo=True)

            def _scan_agents(self, now):
                self.agents = [row]
                self._agents_scan = now

            def toggle_demo(self):
                self.started = time.monotonic()
                self._repaint()

            def _on_console_click(self, event):
                for kind,x0,y0,x1,y1,agent in self.agent_rows:
                    if kind == 'row' and x0 <= event.x <= x1 and y0 <= event.y <= y1:
                        self.toggle_demo()
                        return True
                return super()._on_console_click(event)

        widget = Preview()
        def draw_scooter(agent, now, cell_w=None, cell_h=None):
            return scooter_motion.render(core.state_colour('working'),
                cell_w or core.CELL_W, cell_h or core.CELL_H,
                time.monotonic()-widget.started)
        core.row_figure = draw_scooter
        refresh_menu = widget.tray.refresh_menu
        def menu_with_replay(menu):
            refresh_menu(menu)
            for item in menu.itemArray():
                if item.title() == 'Demo figures':
                    item.setTitle_('Replay scooter action')
                    item.setState_(0)
        widget.tray.refresh_menu = menu_with_replay
        menu_with_replay(widget.tray.menu)

        def verify():
            widget._repaint()
            assert len(widget.agents) == 1
            assert widget.root.panel.isVisible()
            assert widget.root.view.image is not None
            assert any(item.title() == 'Replay scooter action'
                       for item in widget.tray.menu.itemArray())
            print('READY: one visible scooter, 20 FPS, click row or menu to replay', flush=True)
        widget.root.after(900, verify)
        widget.run()


if __name__ == '__main__':
    main()
