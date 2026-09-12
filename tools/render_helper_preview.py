"""Export the real six helper animations as a self-contained browser preview."""
import base64
import io
import json
from pathlib import Path
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smith_agents import artwork, figure_actions


def main():
    destination = Path(sys.argv[1] if len(sys.argv) > 1 else 'design/little-smith-animated.html')
    items = []
    colors = ('#df916d', '#df916d', '#df916d', '#e5bd69', '#9ebd9a', '#a8a39b')
    for name, color in zip(artwork.HELPER_FIGURES, colors):
        sheet = Image.new('RGBA', (16*216, 8*168))
        for frame in range(128):
            drawing = artwork.render(name, color, 216, 168, frame=frame)
            sheet.paste(drawing, ((frame%16)*216, (frame//16)*168))
        buffer = io.BytesIO()
        sheet.save(buffer, format='PNG', optimize=True)
        title, action = figure_actions.LABELS[name]
        items.append(dict(title=title, action=action, color=color,
                          image='data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()))
    document = '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Little Smith · six animated states</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#151310;color:#eee7df;font:16px system-ui,sans-serif}
main{max-width:1120px;margin:auto;padding:40px 28px}header{display:flex;justify-content:space-between;gap:24px;align-items:center}
h1{font-size:32px;font-weight:550;margin:6px 0 10px}p{color:#b7b0a7;line-height:1.5;margin:0}.eyebrow{font-size:12px;letter-spacing:.17em;color:#df916d}
nav{display:flex;gap:10px}button{background:#302b24;color:#eee7df;border:1px solid #574c3e;border-radius:8px;padding:10px 16px;font:inherit;cursor:pointer}
button:hover{border-color:#df916d}button:focus-visible{outline:2px solid #df916d;outline-offset:3px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:28px}.card{background:#201c17;border:1px solid #39322a;border-radius:14px;padding:22px;cursor:pointer}
.stage{display:grid;place-items:center;min-height:186px}canvas{max-width:100%;height:auto}h2{font-weight:500;font-size:20px;margin:14px 0 6px}.small{display:flex;align-items:center;gap:16px;border-top:1px solid #3b332a;margin-top:20px;padding-top:14px;font-size:12px;color:#aaa298}
footer{margin-top:24px;color:#a99e90;font-size:13px}#phase{display:inline-block;min-width:145px}input{accent-color:#df916d;vertical-align:middle;margin-right:6px}
@media(max-width:780px){.grid{grid-template-columns:repeat(2,1fr)}header{align-items:start;flex-direction:column}}@media(max-width:480px){.grid{grid-template-columns:1fr}}
</style><main><header><div><div class="eyebrow">SMITH AGENTS · LITTLE SMITH</div><h1>One helper. Six states.</h1><p>Click a figure to replay. Each action settles into a quiet idle.</p></div>
<nav><button id="replay">Replay all</button><button id="pause" aria-pressed="false">Pause</button></nav></header><div class="grid" id="grid"></div>
<footer><span id="phase">Playing entrance</span><label><input type="checkbox" id="loop" checked>Repeat full actions</label><p>Rendered by the widget’s own animation code. The smaller figure shows its compact display size.</p></footer></main>
<script>const items=ITEM_DATA;
let paused=matchMedia('(prefers-reduced-motion: reduce)').matches, previous=performance.now(), time=0;
const cards=items.map(item=>{const el=document.createElement('article');el.className='card';el.tabIndex=0;el.setAttribute('role','button');el.setAttribute('aria-label','Replay '+item.title);
el.innerHTML='<div class="stage"><canvas width="216" height="168"></canvas></div><h2></h2><p></p><div class="small"><canvas width="216" height="168" style="width:72px;height:56px"></canvas><span>Compact size</span></div>';
el.querySelector('h2').textContent=item.title;el.querySelector('h2').style.color=item.color;el.querySelector('p').textContent=item.action;document.querySelector('#grid').append(el);
const image=new Image();image.src=item.image;const card={image,offset:0,canvases:[...el.querySelectorAll('canvas')]};
el.onclick=()=>{card.offset=time;paused=false;updateButton()};el.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();el.click()}};return card});
function updateButton(){document.querySelector('#pause').textContent=paused?'Play':'Pause';document.querySelector('#pause').setAttribute('aria-pressed',String(paused))}
document.querySelector('#pause').onclick=()=>{paused=!paused;updateButton()};document.querySelector('#replay').onclick=()=>{cards.forEach(c=>c.offset=time);paused=false;updateButton()};updateButton();
function tick(now){const delta=Math.min(100,now-previous)/1000;previous=now;if(!paused)time+=delta;
cards.forEach(c=>{let age=time-c.offset;if(document.querySelector('#loop').checked)age%=6.4;const f=Math.floor(age*20);const frame=f<64?f:64+(f-64)%64;
if(c.image.complete&&c.image.naturalWidth)c.canvases.forEach(canvas=>{const ctx=canvas.getContext('2d');ctx.clearRect(0,0,216,168);ctx.drawImage(c.image,(frame%16)*216,Math.floor(frame/16)*168,216,168,0,0,216,168)})});
let age=time-cards[0].offset;if(document.querySelector('#loop').checked)age%=6.4;document.querySelector('#phase').textContent=paused?'Paused':(age<3.2?'Playing entrance':'Quiet idle');requestAnimationFrame(tick)}requestAnimationFrame(tick);
</script></html>'''.replace('ITEM_DATA', json.dumps(items))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document)
    print(destination.resolve())


if __name__ == '__main__':
    main()
