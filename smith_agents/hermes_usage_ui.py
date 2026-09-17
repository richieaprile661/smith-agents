"""Compact saved-session readings, sharing the widget's type and colours."""
from . import core as c, hermes_usage as usage


def tokens(value):
    return c.compact_tokens(value) if value is not None else '—'


def square_readings(data):
    """Magnitude fields with explicit units per cell, not invented quotas.

    Each grid has 100 cells. Increase the unit by powers of ten so the
    saved reading fits; partial cells round down to avoid overstating usage.
    """
    session = (data or {}).get('session', {})
    readings = []
    for label, key, unit in [('Tokens', 'tokens', 1000),
                             ('Est. cost', 'estimated_cost_usd', .01)]:
        value = session.get(key)
        while value is not None and value > unit * 100:
            unit *= 10
        metric = {'pct': min(100, int(value / unit + 1e-9))} if value is not None else None
        scale = tokens(unit) if key == 'tokens' else ('$%g' % unit if unit >= 1 else '%g¢' % (unit * 100))
        readings.append((label, metric, scale + '/□' if value is not None else '—'))
    return readings


def _amounts(data):
    session = (data or {}).get('session', {})
    return tokens(session.get('tokens')), usage.cost(session.get('estimated_cost_usd'))


def _square(pen, reading, value, center, top, label_y, value_y, scale_y):
    label, metric, scale = reading
    font = c.FONT('bold', 9)
    pen.text((center-c.text_w(label, font)//2, label_y), label, font=font, fill=c._ink(100))
    c.draw_signal_field(pen, metric, 'hermes', center-c.px(29)//2, top)
    ink = c._tok('fg') if c.THEME_NAME == 'eink' else (255, 255, 255, 255)
    font = c.MONO('bold', 10)
    value = c.elide(value, font, c.px(39))
    pen.text((center-c.text_w(value, font)//2, value_y), value, font=font, fill=ink)
    font = c.FONT('book', 7)
    pen.text((center-c.text_w(scale, font)//2, scale_y), scale, font=font, fill=ink)


def header(pen, data, stale=False):
    for index, (reading, value) in enumerate(zip(square_readings(data), _amounts(data))):
        _square(pen, reading, value, c.px(116+42*index), c.px(24), c.px(8), c.px(56), c.px(68))
    text = 'Last reading' if stale else 'Session · auto scale'
    font = c.FONT('book', 6)
    pen.text((c.px(137)-c.text_w(text, font)//2, c.px(79)), text, font=font, fill=c._ink(55))


def panel(pen, data, y, stats_only=False):
    boxes = []
    session = data.get('session', {})
    width = c.CONSOLE_W-2*c.PAD_X
    c.rule(pen, y, c._ink(13))

    def line(text, offset, bold=False, faint=False):
        font = c.FONT('bold' if bold else 'book', 10)
        pen.text((c.PAD_X, y+c.px(offset)), c.elide(text, font, width),
                 font=font, fill=c._ink(62 if faint else 100))

    def pager(label, offset, count, index, kind, limited=False):
        suffix = '+' if limited else ''
        line('%s %d / %d%s' % (label, index+1, count, suffix), offset, faint=True)
        if count > 1:
            for direction, x, text in [(-1, c.CONSOLE_W-c.PAD_X-c.px(62), '‹'),
                                       (1, c.CONSOLE_W-c.PAD_X-c.px(24), '›')]:
                pen.text((x+c.px(8), y+c.px(offset-2)), text, font=c.FONT('bold', 14), fill=c._ink(100))
                boxes.append((kind, x, y+c.px(offset-4), x+c.px(24), y+c.px(offset+16), direction))

    if not session:
        line('No saved Hermes sessions', 14)
        line('Usage appears after Hermes saves it.', 34, faint=True)
        return boxes
    pager('Saved session', 10, len(data['sessions']), data['session_index'], 'hermes-session', data.get('limited'))
    line(usage.session_name(session), 34, bold=True)
    line(session.get('title') or session.get('model') or 'Hermes', 52, faint=True)
    if stats_only:
        for offset, key, label in [(78, 'api_call_count', 'API calls'), (99, 'tool_call_count', 'Tool calls'),
                                    (120, 'message_count', 'Messages')]:
            value = session.get(key)
            line(label+'   '+('{:,}'.format(value) if value is not None else '—'), offset)
        return boxes
    line('%s tokens  ·  %s est.' % (tokens(session.get('tokens')), usage.cost(session.get('estimated_cost_usd'))), 76, bold=True)
    line('In %s  ·  Out %s' % (tokens(session.get('input_tokens')), tokens(session.get('output_tokens'))), 96)
    line('Cache read %s  ·  Write %s' % (tokens(session.get('cache_read_tokens')), tokens(session.get('cache_write_tokens'))), 114, faint=True)
    line('Reasoning %s' % tokens(session.get('reasoning_tokens')), 132, faint=True)
    c.rule(pen, y+c.px(155), c._ink(13))
    models = session.get('models', [])
    if models:
        pager('Model', 165, len(models), data['model_index'], 'hermes-model', session.get('models_limited'))
        model = data['model']
        line(model.get('model') or 'Unknown model', 189, bold=True)
        line(' · '.join(v for v in (model.get('billing_provider'), model.get('task')) if v) or 'Provider not recorded', 207, faint=True)
        line('%s tokens  ·  %s est.' % (tokens(model.get('tokens')), usage.cost(model.get('estimated_cost_usd'))), 231)
        line('In %s  ·  Out %s' % (tokens(model.get('input_tokens')), tokens(model.get('output_tokens'))), 251, faint=True)
    else:
        line('Model breakdown not recorded', 175, faint=True)
    line('Saved totals · cost estimate, not a bill', 280, faint=True)
    return boxes


def drawer(pen, data, width, horizontal, notice):
    session = (data or {}).get('session', {})
    for index, (reading, value) in enumerate(zip(square_readings(data), _amounts(data))):
        center = round(width*(.47 if index == 0 else .77)) if horizontal else c.px(24+40*index)
        _square(pen, reading, value, center, c.px(14 if horizontal else 44),
                c.px(3 if horizontal else 29), c.px(44 if horizontal else 76),
                c.px(56 if horizontal else 89))
    if not horizontal:
        font = c.FONT('book', 8)
        for label, y in [('Saved session', 102), ('Auto scale', 113)]:
            pen.text(((width-c.text_w(label, font))//2, c.px(y)), label, font=font, fill=c._ink(62))
        label = 'View sessions ›'
        font = c.FONT('book', 9)
        pen.text(((width-c.text_w(label, font))//2, c.px(125)), label, font=font, fill=c._ink(100))
    if notice:
        font = c.FONT('book', 8)
        label = 'Last reading' if session else 'Unavailable'
        pen.text((c.px(8), c.px(67 if horizontal else 145)), label, font=font, fill=c._ink(62))
    height = c.px(66 if horizontal else 144)
    return [('peek-hermes-usage', 0, c.px(24), width, height, None)]
