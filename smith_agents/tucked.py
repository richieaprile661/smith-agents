"""Compact edge strip and one selected agent panel, using shared controls."""
from dataclasses import dataclass

from PIL import Image, ImageDraw

from . import core as c

EDGES = ('left', 'right', 'top')


def nearest_edge(bounds, x, y):
    left, top, width, _height = bounds
    distances = {
        'left': abs(x-left), 'right': abs(left+width-x), 'top': abs(y-top),
    }
    return min(EDGES, key=distances.get)


class AgentOrder:
    """Keep targets in place during scans and state changes; group helpers."""
    def __init__(self):
        self.ids = []

    def sync(self, agents):
        live = {a['id']: a for a in agents if a.get('id') and a.get('state') != 'closed'}
        self.ids = [identity for identity in self.ids if identity in live]
        self.ids.extend(identity for identity in live if identity not in self.ids)
        rows = []
        for identity in self.ids:
            agent = live[identity]
            if agent.get('sub') and agent.get('parent') in live:
                continue
            rows.append(agent)
            rows.extend(live[child] for child in self.ids
                        if live[child].get('sub') and live[child].get('parent') == identity)
        return rows


@dataclass
class Layout:
    rail: tuple
    panel: tuple | None
    rail_scroll_max: int
    panel_scroll_max: int
    rail_y: int = 0
    rail_x: int = 0
    horizontal: bool = False


def _translated(boxes, x, y):
    return [(kind, x0+x, y0+y, x1+x, y1+y, agent)
            for kind, x0, y0, x1, y1, agent in boxes]


def _name_lines(agent):
    label = ('↳ ' if agent.get('sub') else '') + (agent.get('name') or 'Agent')
    return c.panel_wrap(label, c.FONT('book', 9), c.PEEK_W-c.px(12))


def _usage_label(shown):
    if not shown:
        return 'Usage'
    label = shown['label']
    bucket = shown.get('header_label')
    if bucket and bucket.casefold() not in label.casefold():
        label = bucket+' '+label
    return c.caps(label)


def _draw_counts(pen, agents):
    """Count every selectable session/helper in either strip orientation."""
    font = c.FONT('book', 9)
    for i, (state, colour) in enumerate((('working', 'ff5f57'), ('needs', 'ffbd2e'), ('done', '28c840'))):
        x = c.px(40+i*24)
        pen.ellipse((x-c.px(4), c.px(9), x+c.px(4), c.px(17)), fill=c._rgb(colour))
        value = str(sum(a.get('state') == state for a in agents))
        pen.text((x-c.text_w(value, font)/2, c.px(22)), value, font=font, fill=c._ink(78))


def _agent_cell(agent, width, height, lines, selected, side, now, elapsed, visible=True):
    cell = Image.new('RGBA', (width, height), c._tok('paper'))
    draw = ImageDraw.Draw(cell)
    ink = c.state_colour(agent.get('state'))
    if agent['id'] == selected:
        draw.rectangle((0, 0, width, height), fill=c._tint(ink, 10))
        if side == 'top':
            draw.rectangle((c.px(3), height-c.px(2), width-c.px(3), height), fill=ink)
        else:
            edge = 0 if side == 'right' else width-c.px(2)
            draw.rectangle((edge, c.px(3), edge+c.px(2), height-c.px(3)), fill=ink)
    draw.line((0, height-1, width, height-1), fill=c._ink(8))
    fw, fh = c.px(80), c.px(40)
    figure = c.row_figure(agent, elapsed(agent) if elapsed else now, fw, fh) if visible else None
    if figure:
        cell.alpha_composite(figure, ((width-fw)//2, c.px(4)))
    c.draw_provider_badge(cell, agent, c.px(3), c.px(2))
    font = c.FONT('book', 9)
    for i, line in enumerate(lines):
        draw.text(((width-c.text_w(line, font))//2, c.px(48)+i*c.panel_line_height(font)),
                  line, font=font, fill=c._ink(88))
    return cell


def _strip(agents, metrics, front, now, selected, side, mode, max_height, scroll, elapsed, provider=None):
    width, head, foot = c.PEEK_W, c.px(44), c.px(42)
    name_font = c.FONT('book', 9)
    line_h = c.panel_line_height(name_font)
    rows = []
    content = 0
    for agent in agents:
        lines = _name_lines(agent)
        row_h = c.px(48)+len(lines)*line_h+c.px(5)
        rows.append((agent, content, row_h, lines))
        content += row_h
    content = max(c.px(40), content)
    height = min(head+content+foot, c.px(580), max_height)
    overflow = height < head+content+foot
    arrow_h = c.px(14) if overflow else 0
    top, bottom = head+arrow_h, height-foot-arrow_h
    scroll_max = max(0, content-(bottom-top))
    offset = max(0, min(int(scroll), scroll_max))
    chip = c.peek_base(side, height)
    pen = ImageDraw.Draw(chip)
    boxes = []
    _draw_counts(pen, agents)
    direction = -1 if side == 'right' else 1
    x, y = c.px(12), c.px(21)
    pen.line(((x-direction*c.px(2), y-c.px(4)), (x+direction*c.px(2), y),
              (x-direction*c.px(2), y+c.px(4))), fill=c._ink(78), width=max(1, c.px(1)))
    boxes.append(('peek-expand', 0, 0, width, head, None))
    pen.line((0, head, width, head), fill=c._ink(20))
    if not agents:
        font = c.FONT('book', 9)
        text = 'No agents'
        pen.text(((width-c.text_w(text, font))/2, top+c.px(12)), text, font=font, fill=c._ink(52))
    selected_y = top-offset
    for agent, row_top, row_h, lines in rows:
        y = top+row_top-offset
        if agent['id'] == selected:
            selected_y = y
        if y >= bottom or y+row_h <= top:
            continue
        cell = _agent_cell(agent, width, row_h, lines, selected, side, now, elapsed,
                           visible=y+c.px(44) > top)
        lo, hi = max(top, y), min(bottom, y+row_h)
        chip.paste(cell.crop((0, lo-y, width, hi-y)), (0, lo))
        boxes.append(('peek-agent', 0, lo, width, hi, agent))
    if overflow:
        for kind, y, enabled, sign in (('peek-up', head, offset > 0, -1),
                                       ('peek-down', bottom, offset < scroll_max, 1)):
            mid = y+arrow_h//2
            pen.line(((width//2-c.px(3), mid-sign*c.px(2)), (width//2, mid+sign*c.px(2)),
                      (width//2+c.px(3), mid-sign*c.px(2))),
                     fill=c._ink(78 if enabled else 25), width=max(1, c.px(1)))
            if enabled:
                boxes.append((kind, 0, y, width, y+arrow_h, None))
    shown = c.bar_reading(metrics, front, mode)
    value = '%d%%' % round(shown['pct']) if shown else '—'
    label = _usage_label(shown)
    font, small = c.FONT('semi', 14), c.FONT('book', 8)
    label = c.elide(label, small, width-c.px(8))
    suffix = 'used' if shown else ''
    total = c.text_w(value, font)+c.px(5)+c.text_w(suffix, small)+(c.px(18) if provider else 0)
    left = max(c.px(4), (width-total)//2)
    pen.line((0, height-foot, width, height-foot), fill=c._ink(20))
    if provider:
        logo = c._provider_logo(provider, c.px(16))
        if logo:
            chip.alpha_composite(logo, (left, height-foot+c.px(5)))
        boxes.insert(0, ('provider:toggle', left, height-foot+c.px(2),
                         left+c.px(18), height-foot+c.px(23), None))
        left += c.px(18)
    pen.text((left, height-foot+c.px(4)), value, font=font,
             fill=c.usage_tone(shown['pct']) if shown else c._ink(52))
    pen.text((left+c.text_w(value, font)+c.px(5), height-foot+c.px(9)),
             suffix, font=small, fill=c._ink(78))
    pen.text(((width-c.text_w(label, small))//2, height-foot+c.px(26)),
             label, font=small, fill=c._ink(78))
    boxes.append(('reading', 0, height-foot, width, height, None))
    return chip, boxes, scroll_max, selected_y


def _panel(agent, now, details, confirm, max_height, scroll, elapsed):
    head, foot = c.px(26), c.FOOT_H
    row_h = c.agent_row_height(agent)
    content_h = row_h+(c.agent_drawer_height(agent) if details and not confirm else 0)
    height = min(max_height, head+content_h+foot)
    viewport = height-head-foot
    scroll_max = max(0, content_h-viewport)
    offset = max(0, min(int(scroll), scroll_max))
    content = c.console_base((c.CONSOLE_W, content_h))
    draw = ImageDraw.Draw(content)
    figure_visible = offset < c.px(8)+c.ROW_FIGURE_H
    boxes = c.render_row(draw, content, agent, 0, now, details, confirm,
                         figure_elapsed=elapsed(agent) if figure_visible and elapsed else None,
                         draw_figure=figure_visible)
    if details and not confirm:
        boxes += c.render_drawer(draw, agent, row_h, now)
    chip = c.console_base((c.CONSOLE_W, height))
    chip.paste(content.crop((0, offset, c.CONSOLE_W, offset+viewport)), (0, head))
    draw = ImageDraw.Draw(chip)
    draw.text((c.PAD_X, c.px(7)), 'Agent panel', font=c.FONT('book', 9), fill=c._ink(62))
    right = c.CONSOLE_W-c.PAD_X
    x, y = right-c.px(3), c.px(12)
    for sign in (-1, 1):
        draw.line((x-c.px(3), y-sign*c.px(3), x+c.px(3), y+sign*c.px(3)),
                  fill=c._ink(78), width=max(1, c.px(1)))
    visible = [('peek-close', right-c.px(14), 0, c.CONSOLE_W, head, None)]
    for kind, x0, y0, x1, y1, row in boxes:
        y0, y1 = y0-offset+head, y1-offset+head
        if kind == 'row':
            y0, y1 = max(head, y0), min(height-foot, y1)
        if head <= y0 < y1 <= height-foot:
            visible.append((kind, x0, y0, x1, y1, row))
    label = 'Tuck panel away'
    box = c._link(draw, c.PAD_X, height-foot+c.px(7), label, c.FONT('book', 9), c._ink(78), True)
    visible.append(('peek-close',)+box+(None,))
    if scroll_max:
        for kind, text, x, enabled in (('peek-panel-up', 'Up', right-c.px(52), offset > 0),
                                       ('peek-panel-down', 'Down', right-c.px(25), offset < scroll_max)):
            box = c._link(draw, x, height-foot+c.px(7), text, c.FONT('book', 9), c._ink(78 if enabled else 25))
            if enabled:
                visible.insert(0, (kind,)+box+(None,))
    return chip, visible, scroll_max


def _top_strip(agents, metrics, front, now, selected, mode, max_width, scroll, elapsed, provider=None):
    tile_w, cap_w, arrow = c.PEEK_W, c.PEEK_W, c.px(16)
    font = c.FONT('book', 9)
    lines = [_name_lines(agent) for agent in agents]
    height = max(c.px(80), c.px(53)+max((len(row) for row in lines), default=1)*c.panel_line_height(font))
    content = max(1, len(agents))*tile_w
    width = min(max_width, c.px(920), cap_w+content)
    overflow = width < cap_w+content
    start, end = cap_w+(arrow if overflow else 0), width-(arrow if overflow else 0)
    maximum = max(0, content-(end-start))
    offset = max(0, min(int(scroll), maximum))
    chip = c.console_base((width, height))
    pen = ImageDraw.Draw(chip)
    boxes = [('peek-expand', 0, 0, cap_w, c.px(44), None)]
    _draw_counts(pen, agents)
    pen.line(((c.px(8), c.px(18)), (c.px(12), c.px(22)), (c.px(16), c.px(18))),
             fill=c._ink(78), width=max(1, c.px(1)))
    shown = c.bar_reading(metrics, front, mode)
    value = '%d%%' % round(shown['pct']) if shown else '—'
    label = _usage_label(shown)
    pen.text((c.px(12), c.px(47)), value, font=c.FONT('semi', 14),
             fill=c.usage_tone(shown['pct']) if shown else c._ink(52))
    if shown:
        pen.text((c.px(16)+c.text_w(value, c.FONT('semi', 14)), c.px(53)),
                 'used', font=c.FONT('book', 8), fill=c._ink(78))
    label_x = c.px(12)
    if provider:
        logo = c._provider_logo(provider, c.px(14))
        if logo:
            chip.alpha_composite(logo, (c.px(9), c.px(64)))
        boxes.insert(0, ('provider:toggle', c.px(7), c.px(62), c.px(25), c.px(80), None))
        label_x = c.px(26)
    label_font = c.FONT('book', 8)
    pen.text((label_x, c.px(66)), c.elide(label, label_font, cap_w-label_x-c.px(5)),
             font=label_font, fill=c._ink(78))
    boxes.append(('reading', 0, c.px(44), cap_w, height, None))
    pen.line((cap_w, 0, cap_w, height), fill=c._ink(20))
    selected_x = start-offset
    if not agents:
        pen.text((start+c.px(15), c.px(32)), 'No agents', font=font, fill=c._ink(52))
    for i, agent in enumerate(agents):
        x = start+i*tile_w-offset
        if agent['id'] == selected:
            selected_x = x
        if x >= end or x+tile_w <= start:
            continue
        cell = _agent_cell(agent, tile_w, height, lines[i], selected, 'top', now, elapsed)
        lo, hi = max(start, x), min(end, x+tile_w)
        chip.paste(cell.crop((lo-x, 0, hi-x, height)), (lo, 0))
        pen.line((hi-1, 0, hi-1, height), fill=c._ink(8))
        boxes.append(('peek-agent', lo, 0, hi, height, agent))
    if overflow:
        for kind, x, enabled, sign in (('peek-up', cap_w, offset > 0, -1),
                                       ('peek-down', end, offset < maximum, 1)):
            cx, cy = x+arrow//2, height//2
            pen.line(((cx-sign*c.px(2), cy-c.px(4)), (cx+sign*c.px(2), cy),
                      (cx-sign*c.px(2), cy+c.px(4))), fill=c._ink(78 if enabled else 25),
                     width=max(1, c.px(1)))
            if enabled:
                boxes.append((kind, x, 0, x+arrow, height, None))
    return chip, boxes, maximum, selected_x


def _render_top(agents, metrics, front, now, mode, max_height, max_width, selected,
                scroll, panel_scroll, details, confirm_id, elapsed, rail_x, provider=None):
    rail, boxes, maximum, selected_x = _top_strip(agents, metrics, front, now, selected,
                                                mode, max_width, scroll, elapsed, provider)
    rail_x = max(0, min(int(rail_x), max_width-rail.width))
    pad, gap = c.SHADOW_PAD, c.px(12)
    chosen = next((a for a in agents if a['id'] == selected), None)
    if chosen is None:
        return c.with_shadow(rail), boxes, Layout((pad, pad, pad+rail.width, pad+rail.height),
                                                 None, maximum, 0, 0, rail_x, True)
    panel, controls, panel_max = _panel(chosen, now, details, selected == confirm_id,
                                       max(c.px(80), max_height-rail.height-gap), panel_scroll, elapsed)
    panel_x = max(0, min(rail_x+selected_x, max_width-panel.width))
    left = min(rail_x, panel_x)
    rx, px = rail_x-left, panel_x-left
    py = rail.height+gap
    image = Image.new('RGBA', (max(rx+rail.width, px+panel.width)+2*pad, py+panel.height+2*pad))
    image.alpha_composite(c.with_shadow(rail), (rx, 0))
    image.alpha_composite(c.with_shadow(panel), (px, py))
    return image, _translated(boxes, rx, 0)+_translated(controls, px, py), Layout(
        (rx+pad, pad, rx+pad+rail.width, pad+rail.height),
        (px+pad, py+pad, px+pad+panel.width, py+pad+panel.height), maximum, panel_max, 0, rail_x, True)


def render(agents, metrics, front, now, side='right', mode='worst', max_height=None,
           selected=None, scroll=0, panel_scroll=0, details=True, confirm_id=None,
           figure_elapsed=None, rail_y=0, rail_x=0, max_width=None, provider=None):
    max_height = max(c.px(140), int(max_height or c.px(580)))
    if side == 'top':
        return _render_top(agents, metrics, front, now, mode, max_height,
                           max_width or c.px(920), selected, scroll, panel_scroll,
                           details, confirm_id, figure_elapsed, rail_x, provider)
    rail, boxes, scroll_max, selected_y = _strip(agents, metrics, front, now, selected, side, mode,
                                           max_height, scroll, figure_elapsed, provider)
    rail_y = max(0, min(int(rail_y), max_height-rail.height))
    selected_agent = next((a for a in agents if a.get('id') == selected), None)
    if selected_agent is None:
        pad = c.SHADOW_PAD
        return c.with_shadow(rail), boxes, Layout((pad, pad, pad+rail.width, pad+rail.height), None, scroll_max, 0, rail_y)
    panel, panel_boxes, panel_max = _panel(selected_agent, now, details, selected == confirm_id,
                                          max_height, panel_scroll, figure_elapsed)
    panel_y = max(0, min(rail_y+selected_y, max_height-panel.height))
    top = min(rail_y, panel_y)
    ry, y = rail_y-top, panel_y-top
    gap = c.px(12)
    rx, px = (panel.width+gap, 0) if side == 'right' else (0, rail.width+gap)
    height = max(ry+rail.height, panel.height+y)
    # Each part retains its own rounded border and shadow across the gap.
    pad = c.SHADOW_PAD
    canvas = Image.new('RGBA', (rail.width+panel.width+gap+2*pad, height+2*pad))
    canvas.alpha_composite(c.with_shadow(rail), (rx, ry))
    canvas.alpha_composite(c.with_shadow(panel), (px, y))
    boxes = _translated(boxes, rx, ry)+_translated(panel_boxes, px, y)
    return canvas, boxes, Layout((rx+pad, ry+pad, rx+pad+rail.width, ry+pad+rail.height),
                                 (px+pad, y+pad, px+pad+panel.width, y+pad+panel.height), scroll_max, panel_max, rail_y)
