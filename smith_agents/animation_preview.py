"""Full native review: python -m smith_agents.animation_preview.

Uses the real renderer and visibility timeline with isolated sample settings.
Click a row to replay its action; the tray's replay item restarts all figures.
"""
import argparse
import json
import os
from pathlib import Path
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--smoke-test', action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='widget-animation-preview-') as directory:
        os.environ['SMITH_AGENTS_CONFIG_DIR'] = directory
        Path(directory, 'config.json').write_text(json.dumps({
            'dock': 'top-left', 'zoom': 1.0, 'theme': 'claude',
            'console_open': True, 'console_tab': 'agents',
            'visible': True, 'tucked': False,
        }))
        from . import core, figure_actions
        core.APP_NAME = 'Figure animation preview'
        from . import app

        class Preview(app.SmithAgentsWidget):
            def _scan_agents(self, now):
                self.agents = core.demo_agents(all_figures=True)
                for row in self.agents:
                    pose = core.agent_style(row)[0]
                    label, description = figure_actions.LABELS[pose]
                    row.update(name=label + ' · click to replay',
                               tail=[('cmd', description)])
                self._agents_scan = now

            def toggle_demo(self):
                self._figure_timeline.replay()
                self._repaint()

            def _on_console_click(self, event):
                for kind, x0, y0, x1, y1, agent in self.agent_rows:
                    if kind == 'row' and x0 <= event.x <= x1 and y0 <= event.y <= y1:
                        self._figure_timeline.replay(('row', agent['id']))
                        self._repaint()
                        return True
                return super()._on_console_click(event)

        widget = Preview(demo=True)
        if app.sys.platform == 'darwin':
            refresh_menu = widget.tray.refresh_menu

            def menu_with_replay(menu):
                refresh_menu(menu)
                for item in menu.itemArray():
                    if item.title() == 'Demo figures':
                        item.setTitle_('Replay all figure actions')
                        item.setState_(0)

            widget.tray.refresh_menu = menu_with_replay
            menu_with_replay(widget.tray.menu)

        errors = []

        def verify():
            try:
                from types import SimpleNamespace
                widget._repaint()
                assert len(widget.agents) == 13
                assert len({core.agent_style(row)[0] for row in widget.agents}) == 13
                if app.sys.platform == 'darwin':
                    assert widget.root.panel.isVisible()
                    assert widget.root.view.image is not None
                initial = set(widget._figure_timeline.starts)
                assert 1 < len(initial) < 14, 'Offscreen figures consumed their entrances'
                row_box = next(box for box in widget.agent_rows if box[0] == 'row')
                key = ('row', row_box[-1]['id'])
                before = widget._figure_timeline.starts[key][1]
                widget._on_console_click(SimpleNamespace(
                    x=(row_box[1]+row_box[3])/2, y=(row_box[2]+row_box[4])/2))
                assert widget._figure_timeline.starts[key][1] > before
                for row in core.sort_agents(widget.agents):
                    widget._reveal_agent(row)
                    widget._repaint()
                    assert ('row', row['id']) in widget._figure_timeline.starts
                assert len(widget._figure_timeline.starts) == 14
                widget._agent_scroll = 0
                widget.toggle_demo()
                print('READY: 13 poses + selfie; first-visible entrances, scrolling, '
                      'row replay and replay-all verified', flush=True)
            except Exception:
                import traceback
                errors.append(traceback.format_exc())
                print(errors[-1], flush=True)
            finally:
                if args.smoke_test or errors:
                    widget.quit()

        widget.root.after(900, verify)
        widget.run()
        return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
