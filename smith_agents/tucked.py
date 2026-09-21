"""Compact edge strip and one selected agent panel, using shared controls."""
from dataclasses import dataclass

from PIL import Image, ImageDraw

from . import core as c

EDGES = ('left', 'right', 'top', 'bottom')
RAIL_W = c.px(88)
TILE_W = c.px(108)
TOP_RAIL_H = c.px(64)
TOP_BRAND_W = c.px(88)
FIGURE_W, FIGURE_H = c.px(80), c.px(40)
NAME_SIZE = 9
LAMP_RISE = c.px(18 * 0.2)
SIDE_HEAD_H = c.px(64)
PANEL_HEAD_H = c.px(26)
PANEL_SCROLL_H = c.px(24)


def nearest_edge(bounds, x, y):
    left, top, width, height = bounds
    distances = {
        'left': abs(x-left), 'right': abs(left+width-x), 'top': abs(y-top),
        'bottom': abs(top+height-y),
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
    panel_viewport_height: int = 0


def _translated(boxes, x, y):
    return [(kind, x0+x, y0+y, x1+x, y1+y, agent)
            for kind, x0, y0, x1, y1, agent in boxes]


def _credits(provider, usage_data, horizontal=False):
    """The Codex credit line the drawer carries, if the account has credits.
    The 88pt rail has room for the balance alone; there the accent colour
    says it is in use. The wide strip spells it out."""
    if provider != 'codex' or not isinstance(usage_data, dict) or usage_data.get('provider') != 'codex':
        return None
    line = usage_data['text'] + ' credits'
    return line + (' · in use' if horizontal and usage_data.get('on_credits') else '')


def _usage_height(horizontal, notice, credits=None):
    return c.px((66 if horizontal else 144) + (14 if notice else 0) + (14 if credits else 0))


def _usage_drawer(metrics, front, mode, provider, notice, width=RAIL_W, horizontal=False, usage_data=None):
    """Drawer content; the strip supplies its shared border and shadow."""
    credits = _credits(provider, usage_data, horizontal)
    height = _usage_height(horizontal, notice, credits)
    card = Image.new('RGBA', (width, height), c._tok('paper'))
    pen = ImageDraw.Draw(card)
    metric = c.bar_reading(metrics, front, mode)
    label = metric.get('label', '') if metric else ''
    # The title is the provider alone. Its window - 5h, week, Opus - used to
    # ride along after a dot and was the part elided away on an 88pt rail, so
    # the one thing the drawer exists to tell you never fit. It is the
    # caption under the number now, where the full width is free.
    title = c.provider_name({'provider': provider})
    title_width = min(width//3-c.px(16), c.px(62)) if horizontal else width-c.px(30)
    title_font = c.FONT('bold', 9)
    pen.text((c.px(8), c.px(8 if not horizontal else 25)),
             c.elide(title, title_font, title_width), font=title_font, fill=c._ink(100))
    x, y = width-c.px(12), c.px(12)
    for sign in (-1, 1):
        pen.line((x-c.px(3), y-sign*c.px(3), x+c.px(3), y+sign*c.px(3)),
                 fill=c._ink(62), width=max(1, c.px(1)))
    if provider == 'hermes':
        from .hermes_usage_ui import drawer
        controls = drawer(pen, usage_data, width, horizontal, notice)
        return card, [('peek-usage-close', width-c.px(24), 0, width, c.px(24)-1, None)] + controls
    if horizontal:
        columns, cell, gap = (25, 3, 3) if width >= c.px(300) else (20, 2, 2)
        field_w = c.px(columns*cell+(columns-1)*gap)
        c.draw_signal_field(pen, metric, provider, (width-field_w)//2, c.px(24), cell, gap, columns)
        center, value_y, caption_y = width-c.px(34), 20, 45
    else:
        c.draw_signal_field(pen, metric, provider, (width-c.px(67))//2, c.px(27), 4, 3)
        center, value_y, caption_y = width//2, 105, 130
    value = c.header_reading_value(metric, 'used') if metric else '—'
    font = c.MONO('bold', 22)
    pen.text((center-c.text_w(value, font)//2, c.px(value_y)), value,
             font=font, fill=c.provider_accent(provider) if metric else c._ink(62))
    caption = ((label + ' used') if label else 'used') if metric else 'Unavailable'
    font = c.FONT('book', 9)
    pen.text((center-c.text_w(caption, font)//2, c.px(caption_y)), caption, font=font, fill=c._ink(62))
    # Footer lines stack up from the bottom: the credit balance, then a notice.
    footer = height-c.px(15)
    if credits:
        credits = c.elide(credits, font, width-c.px(8))
        pen.text(((width-c.text_w(credits, font))//2, footer), credits, font=font,
                 fill=c.provider_accent(provider) if usage_data.get('on_credits') else c._ink(78))
        footer -= c.px(14)
    if notice:
        text = 'Not connected yet' if provider == 'hermes' else 'Last reading' if metric else 'Try again later'
        pen.text(((width-c.text_w(text, font))//2, footer), text, font=font, fill=c._ink(62))
    boxes = [('peek-usage-close', width-c.px(24), 0, width, c.px(24)-1, None)]
    if len(metrics) > 1:
        boxes.append(('peek-usage-cycle', 0, c.px(24), width, height, None))
        boxes.append(('peek-usage-cycle', 0, 0, width-c.px(24)-1, c.px(24)-1, None))
    return card, boxes


def _with_usage(image, boxes, layout, metrics, front, mode, provider, notice,
                side, max_height, usage_data=None):
    pad = c.SHADOW_PAD
    width, height = image.width-2*pad, image.height-2*pad
    card, controls = _usage_drawer(metrics, front, mode, provider, notice,
                                 width, layout.horizontal, usage_data)
    above = side == 'bottom' or (not layout.horizontal
                                and layout.rail_y+height+card.height > max_height)
    ry, cy = (card.height, 0) if above else (0, height)
    shell = Image.new('RGBA', (width, height+card.height), c._tok('paper'))
    shell.paste(image.crop((pad, pad, pad+width, pad+height)), (0, ry))
    shell.paste(card, (0, cy))
    seam = card.height if above else height
    ImageDraw.Draw(shell).line((c.EDGE, seam, width-c.EDGE-1, seam), fill=c._ink(20))
    return c.with_shadow(_finish_shell(shell)), _translated(boxes, 0, ry)+_translated(controls, 0, cy), Layout(
        (pad, ry+pad, pad+width, ry+pad+height),
        (pad, cy+pad, pad+width, cy+pad+card.height),
        layout.rail_scroll_max, 0, layout.rail_y, layout.rail_x, layout.horizontal)


def _finish_shell(chip):
    """Keep scrolling content inside the same rounded border as the widget."""
    shell = c.console_base(chip.size)
    mask = Image.new('L', chip.size)
    edge = c.EDGE
    ImageDraw.Draw(mask).rounded_rectangle(
        (edge, edge, chip.width-edge-1, chip.height-edge-1),
        radius=max(0, c.px(8)-edge) if c.T['radius'] else 0, fill=255)
    shell.paste(chip, (0, 0), mask)
    return shell


def _name_lines(agent, width=None):
    label = ('↳ ' if agent.get('sub') else '') + (agent.get('name') or 'Agent')
    label = ' '.join(label.split())[:2000]
    font = c.FONT('book', NAME_SIZE)
    width = (TILE_W if width is None else width)-c.px(12)
    lines = list(c.panel_wrap(label, font, width))
    if len(lines) > 2:
        lines = [lines[0], c.elide(label[len(lines[0]):].lstrip(), font, width)]
    return lines


def _brand(chip, side, provider):
    """Only the Smith icon and bare provider lamps are shown while tucked."""
    horizontal = side in ('top', 'bottom')
    top_edge = side == 'top'
    boxes = []
    # Beside the lamps, Matrix's lettering must stay clear of them.
    icon = c.smith_header_icon(c.px(44) if c.PORTRAITS and top_edge else c.px(64),
                               c.px(32), c._tok('fg'))
    if horizontal:
        icon_x = 0 if top_edge else (TILE_W-icon.width)//2
        chip.alpha_composite(icon, (icon_x, (chip.height-icon.height)//2))
        if top_edge:
            lamp_y = (chip.height-c.px(70))//2
            lamp_x = TOP_BRAND_W-c.px(42)
            positions = tuple((lamp_x, lamp_y+c.px(18)*i) for i in range(3))
        else:
            dy = (chip.height-c.px(82))//2
            positions = tuple((TILE_W, dy+c.px(24)*i) for i in range(3))
            boxes.append(('peek-expand', 0, 0, TILE_W, chip.height, None))
    else:
        chip.alpha_composite(icon, ((RAIL_W-icon.width)//2, c.px(28)))
        lamp_x = (RAIL_W-c.px(82))//2
        positions = tuple((lamp_x+c.px(24)*i, -LAMP_RISE) for i in range(3))
        boxes.append(('peek-expand', 0, c.px(28), RAIL_W, SIDE_HEAD_H, None))
    for index, (name, (x, y)) in enumerate(zip(('claude', 'codex', 'hermes'), positions)):
        lamp = c.provider_lamp(name, name == (provider or 'claude'))
        chip.alpha_composite(lamp, (x, y))
        x0, x1, y0, y1 = x, x+lamp.width, max(0, y), y+lamp.height
        if horizontal:
            # Glow canvases overlap; adjacent click targets never do.
            if index:
                y0 = max(y0, (positions[index-1][1]+y+lamp.height)//2)
            if index < len(positions)-1:
                y1 = min(y1, (y+positions[index+1][1]+lamp.height)//2-1)
            y1 = min(y1, chip.height)
        elif not horizontal:
            # Keep adjacent lamps and the Smith icon independently clickable.
            y1 = min(y1, c.px(28)-1)
            if index:
                x0 = max(x0, (positions[index-1][0]+x+lamp.width)//2)
            if index < len(positions)-1:
                x1 = min(x1, (x+positions[index+1][0]+lamp.width)//2-1)
        boxes.append(('provider:'+name, x0, y0, x1, y1, None))
    if not horizontal:
        separator_y = SIDE_HEAD_H-c.px(2)
        ImageDraw.Draw(chip).line((c.px(10), separator_y, RAIL_W-c.px(10)-1, separator_y),
                                 fill=c._ink(13), width=max(1, c.px(1)))
    return boxes


def _cell_height(lines):
    return c.px(53)+len(lines)*c.panel_line_height(c.FONT('book', NAME_SIZE))


# The top of a cell is the portrait; below it is the name. Clicking the face
# brings that session's window forward, clicking the name opens its panel.
FACE_H = c.px(4) + FIGURE_H


def _agent_cell(agent, width, height, lines, selected, side, now, elapsed, visible=True):
    """The original agent tile, shared by side and horizontal strips."""
    cell = Image.new('RGBA', (width, height), c._tok('paper'))
    draw = ImageDraw.Draw(cell)
    ink = c.state_colour(agent.get('state'))
    if agent['id'] == selected:
        draw.rectangle((0, 0, width, height), fill=c._tint(ink, 10))
    # The bottom hairline goes under the edge bars: at 1x a bar on the bottom
    # edge is two pixels tall and the line drawn after it took one of them.
    draw.line((0, height-1, width, height-1), fill=c._ink(8))
    if agent['id'] == selected:
        if side in ('top', 'bottom'):
            y = height-c.px(2) if side == 'top' else 0
            draw.rectangle((c.px(3), y, width-c.px(3), y+c.px(2)), fill=ink)
        else:
            edge = 0 if side == 'right' else width-c.px(2)
            draw.rectangle((edge, c.px(3), edge+c.px(2), height-c.px(3)), fill=ink)
    if agent.get('_window_state') == 'front':
        # The session whose window is in front wears a bar on the cell's outer
        # edge - the selection bar is on the inner edge, in the state colour,
        # so the two never meet. It moves with the OS's answer on every scan.
        fg = c._tok('fg')
        if side in ('top', 'bottom'):
            y = 0 if side == 'top' else height-c.px(2)
            draw.rectangle((c.px(3), y, width-c.px(3), y+c.px(2)), fill=fg)
        else:
            edge = width-c.px(2) if side == 'right' else 0
            draw.rectangle((edge, c.px(3), edge+c.px(2), height-c.px(3)), fill=fg)
    figure = c.row_figure(agent, elapsed(agent) if elapsed else now,
                         FIGURE_W, FIGURE_H) if visible else None
    if figure:
        c.paste_figure(cell, figure, ((width-FIGURE_W)//2, c.px(4)))
    c.draw_provider_badge(cell, agent, c.px(3), c.px(2))
    font = c.FONT('book', NAME_SIZE)
    for i, line in enumerate(lines):
        draw.text(((width-c.text_w(line, font))//2, c.px(48)+i*c.panel_line_height(font)),
                  line, font=font, fill=c._ink(88))
    return cell


def _strip(agents, now, selected, side, max_height, scroll, elapsed, provider, finish=True):
    width, head = RAIL_W, SIDE_HEAD_H
    rows = []
    content = 0
    for agent in agents:
        lines = _name_lines(agent, width)
        row_h = _cell_height(lines)
        rows.append((agent, content, row_h, lines))
        content += row_h
    content = max(c.px(40), content)
    height = min(head+content, c.px(580), max_height)
    overflow = height < head+content
    arrow_h = c.px(16) if overflow else 0
    top, bottom = head+arrow_h, height-arrow_h
    scroll_max = max(0, content-(bottom-top))
    offset = max(0, min(int(scroll), scroll_max))
    chip = c.console_base((width, height)) if finish else Image.new('RGBA', (width, height), c._tok('paper'))
    pen = ImageDraw.Draw(chip)
    boxes = _brand(chip, side, provider)
    pen.line((0, head, width, head), fill=c._ink(20))
    if not agents:
        font = c.FONT('book', NAME_SIZE)
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
                           visible=y+c.px(4)+FIGURE_H > top)
        lo, hi = max(top, y), min(bottom, y+row_h)
        chip.paste(cell.crop((0, lo-y, width, hi-y)), (0, lo))
        # face first: the click handlers take the first box containing the point
        for kind, y0, y1 in (('peek-open', lo, min(hi, y+FACE_H)), ('peek-agent', max(lo, y+FACE_H), hi)):
            if y0 < y1:
                boxes.append((kind, 0, y0, width, y1, agent))
    if overflow:
        for kind, y, enabled, sign in (('peek-up', head, offset > 0, -1),
                                       ('peek-down', bottom, offset < scroll_max, 1)):
            mid = y+arrow_h//2
            pen.line(((width//2-c.px(3), mid-sign*c.px(2)), (width//2, mid+sign*c.px(2)),
                      (width//2+c.px(3), mid-sign*c.px(2))),
                     fill=c._ink(78 if enabled else 25), width=max(1, c.px(1)))
            if enabled:
                boxes.append((kind, 0, y, width, y+arrow_h, None))
    return (_finish_shell(chip) if finish else chip), boxes, scroll_max, selected_y


def _panel(agent, now, details, confirm, max_height, scroll, elapsed):
    head = PANEL_HEAD_H
    row_h = c.agent_row_height(agent, details)
    content_h = row_h+(c.agent_drawer_height(agent) if details and not confirm else 0)
    height = min(max_height, head+content_h)
    foot = PANEL_SCROLL_H if height < head+content_h else 0
    viewport = height-head-foot
    scroll_max = max(0, content_h-viewport)
    offset = max(0, min(int(scroll), scroll_max))
    content = Image.new('RGBA', (c.CONSOLE_W, content_h), c._tok('paper'))
    draw = ImageDraw.Draw(content)
    figure_visible = not c.compact_helper(agent) and offset < c.px(12)+c.ROW_FIGURE_H
    boxes = c.render_row(draw, content, agent, 0, now, details, confirm,
                         figure_elapsed=elapsed(agent) if figure_visible and elapsed else None,
                         draw_figure=figure_visible)
    if details and not confirm:
        boxes += c.render_drawer(draw, agent, row_h, now)
    chip = c.console_base((c.CONSOLE_W, height))
    chip.paste(content.crop((0, offset, c.CONSOLE_W, offset+viewport)), (0, head))
    draw = ImageDraw.Draw(chip)
    c.rule(draw, head, c._ink(20))
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
    if scroll_max:
        draw.line((0, height-foot, c.CONSOLE_W, height-foot), fill=c._ink(20))
        for kind, text, x0, x1, enabled in (
                ('peek-panel-up', 'Up', 0, c.CONSOLE_W//2, offset > 0),
                ('peek-panel-down', 'Down', c.CONSOLE_W//2, c.CONSOLE_W, offset < scroll_max)):
            font = c.FONT('book', 9)
            draw.text(((x0+x1-c.text_w(text, font))//2, height-foot+c.px(6)), text,
                      font=font, fill=c._ink(78 if enabled else 25))
            if enabled:
                visible.append((kind, x0, height-foot, x1, height, None))
    return _finish_shell(chip), visible, scroll_max, viewport


def _horizontal_strip(agents, now, selected, side, max_width, scroll, elapsed, provider, finish=True):
    top_edge = side == 'top'
    tile_w = TILE_W
    cap_w = c.px(8) if top_edge else TILE_W+c.px(36)
    arrow, foot = c.px(16), TOP_BRAND_W if top_edge else c.px(28)
    font = c.FONT('book', NAME_SIZE)
    lines = [_name_lines(agent) for agent in agents]
    height = max(TOP_RAIL_H if top_edge else c.px(86),
                 max((_cell_height(row) for row in lines), default=0))
    content = max(1, len(agents))*tile_w
    width = min(max_width, c.px(920), cap_w+content+foot)
    overflow = width < cap_w+content+foot
    start, end = cap_w+(arrow if overflow else 0), width-foot-(arrow if overflow else 0)
    maximum = max(0, content-(end-start))
    offset = max(0, min(int(scroll), maximum))
    chip = c.console_base((width, height)) if finish else Image.new('RGBA', (width, height), c._tok('paper'))
    pen = ImageDraw.Draw(chip)
    if top_edge:
        brand = Image.new('RGBA', (foot, height), c._tok('paper'))
        brand_boxes = _brand(brand, side, provider)
        chip.alpha_composite(brand, (width-foot, 0))
        boxes = _translated(brand_boxes, width-foot, 0)
        # The Smith icon opens the widget; lamps stay separate.
        boxes.append(('peek-expand', width-foot, 0,
                      width-foot+min(box[1] for box in brand_boxes), height, None))
    else:
        boxes = _brand(chip, side, provider)
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
        cell = _agent_cell(agent, tile_w, height, lines[i], selected, side, now, elapsed)
        lo, hi = max(start, x), min(end, x+tile_w)
        chip.paste(cell.crop((lo-x, 0, hi-x, height)), (lo, 0))
        # A clipped tile is not a container boundary. Draw only real dividers,
        # inset from the shell so its rounded outline stays continuous.
        boundary = x+tile_w
        if top_edge and start < boundary < end:
            pen.rectangle((boundary-c.EDGE, c.px(10), boundary-1, height-c.px(10)-1),
                          fill=c._ink(13))
        elif not top_edge:
            pen.line((hi-1, 0, hi-1, height), fill=c._ink(8))
        boxes.append(('peek-open', lo, 0, hi, FACE_H, agent))
        boxes.append(('peek-agent', lo, FACE_H, hi, height, agent))
    if overflow:
        for kind, x, enabled, sign in (('peek-up', cap_w, offset > 0, -1),
                                       ('peek-down', end, offset < maximum, 1)):
            cx, cy = x+arrow//2, height//2
            pen.line(((cx-sign*c.px(2), cy-c.px(4)), (cx+sign*c.px(2), cy),
                      (cx-sign*c.px(2), cy+c.px(4))), fill=c._ink(78 if enabled else 25),
                     width=max(1, c.px(1)))
            if enabled:
                boxes.append((kind, x, 0, x+arrow, height, None))
    if top_edge:
        pen.rectangle((width-foot-c.EDGE, c.px(10), width-foot-1, height-c.px(10)-1),
                      fill=c._ink(20))
    else:
        pen.line((width-foot, 0, width-foot, height), fill=c._ink(20))
        c._arrow(pen, width-foot//2-c.px(3), height//2-c.px(3), c._ink(78))
        boxes.append(('peek-expand', width-foot, 0, width, height, None))
    return (_finish_shell(chip) if finish else chip), boxes, maximum, selected_x


def _render_horizontal(agents, now, side, max_height, max_width, selected,
                       scroll, panel_scroll, details, confirm_id, elapsed, rail_x, provider, finish=True):
    rail, boxes, maximum, selected_x = _horizontal_strip(
        agents, now, selected, side, max_width, scroll, elapsed, provider, finish)
    rail_x = max(0, min(int(rail_x), max_width-rail.width))
    pad, gap = c.SHADOW_PAD, c.px(12)
    chosen = next((a for a in agents if a['id'] == selected), None)
    if chosen is None:
        return c.with_shadow(rail), boxes, Layout((pad, pad, pad+rail.width, pad+rail.height),
                                                 None, maximum, 0, 0, rail_x, True)
    panel, controls, panel_max, viewport = _panel(
        chosen, now, details, selected == confirm_id,
        max_height-rail.height-gap, panel_scroll, elapsed)
    panel_x = max(0, min(rail_x+selected_x, max_width-panel.width))
    left = min(rail_x, panel_x)
    rx, px = rail_x-left, panel_x-left
    ry, py = (0, rail.height+gap) if side == 'top' else (panel.height+gap, 0)
    image = Image.new('RGBA', (max(rx+rail.width, px+panel.width)+2*pad,
                               rail.height+gap+panel.height+2*pad))
    image.alpha_composite(c.with_shadow(rail), (rx, ry))
    image.alpha_composite(c.with_shadow(panel), (px, py))
    return image, _translated(boxes, rx, ry)+_translated(controls, px, py), Layout(
        (rx+pad, ry+pad, rx+pad+rail.width, ry+pad+rail.height),
        (px+pad, py+pad, px+pad+panel.width, py+pad+panel.height), maximum, panel_max,
        0, rail_x, True, viewport)


def render(agents, metrics, front, now, side='right', mode='worst', max_height=None,
           selected=None, scroll=0, panel_scroll=0, details=True, confirm_id=None,
           figure_elapsed=None, rail_y=0, rail_x=0, max_width=None, provider=None,
           usage_open=False, usage_notice=None, usage_data=None):
    max_height = max(c.px(140), int(max_height or c.px(580)))
    max_width = max_width or c.px(920)
    if usage_open:
        selected = None
    if side in ('top', 'bottom'):
        result = _render_horizontal(agents, now, side, max_height,
                           max_width, selected, scroll, panel_scroll,
                           details, confirm_id, figure_elapsed, rail_x, provider, finish=not usage_open)
        return (_with_usage(*result, metrics, front, mode, provider, usage_notice,
                            side, max_height, usage_data) if usage_open else result)
    rail, boxes, scroll_max, selected_y = _strip(
        agents, now, selected, side,
        max_height-(_usage_height(False, usage_notice, _credits(provider, usage_data)) if usage_open else 0),
        scroll, figure_elapsed, provider, finish=not usage_open)
    rail_y = max(0, min(int(rail_y), max_height-rail.height))
    if usage_open:
        drawer_h = _usage_height(False, usage_notice, _credits(provider, usage_data))
        if rail_y < drawer_h and rail_y+rail.height+drawer_h > max_height:
            rail_y = max(0, max_height-rail.height-drawer_h)
    selected_agent = next((a for a in agents if a.get('id') == selected), None)
    if selected_agent is None:
        pad = c.SHADOW_PAD
        result = c.with_shadow(rail), boxes, Layout((pad, pad, pad+rail.width, pad+rail.height), None, scroll_max, 0, rail_y)
        return (_with_usage(*result, metrics, front, mode, provider, usage_notice,
                            side, max_height, usage_data) if usage_open else result)
    panel, panel_boxes, panel_max, viewport = _panel(selected_agent, now, details, selected == confirm_id,
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
                                 (px+pad, y+pad, px+pad+panel.width, y+pad+panel.height), scroll_max, panel_max, rail_y, panel_viewport_height=viewport)
