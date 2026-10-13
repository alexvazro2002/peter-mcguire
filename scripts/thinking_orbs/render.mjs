// Offline native-client assets from the actual pinned thinking-orbs engine.
import {createCanvas} from '@napi-rs/canvas';
import {MODE_DRAWS, resolvePreset} from 'thinking-orbs/engine';
import {mkdir, writeFile, readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output=path.join(root,'work/orb-frames');
const states=['breathing','listening','working','composing'];
const fps=24, frames=144, blend=12, pixels=192;
function draw(state,time,dark){
  const canvas=createCanvas(pixels,pixels), ctx=canvas.getContext('2d');
  ctx.scale(3,3);
  const preset=resolvePreset(state,64);
  MODE_DRAWS[preset.mode](ctx,64,time*preset.speed,dark,preset.opts);
  return canvas;
}
for(const theme of ['dark','light']){
  for(const state of states){
    const directory=path.join(output,`${theme}-${state}`);
    await mkdir(directory,{recursive:true});
    for(let i=0;i<frames;i++){
      const canvas=draw(state,i/fps,theme==='dark');
      // Crossfade to the start over the final half-second for a seamless loop.
      if(i>=frames-blend){
        const ctx=canvas.getContext('2d'), alpha=(i-(frames-blend)+1)/blend;
        const original=createCanvas(pixels,pixels);
        original.getContext('2d').drawImage(canvas,0,0);
        ctx.clearRect(0,0,pixels,pixels);
        ctx.globalAlpha=1-alpha; ctx.drawImage(original,0,0);
        ctx.globalAlpha=alpha; ctx.drawImage(draw(state,0,theme==='dark'),0,0);
      }
      await writeFile(path.join(directory,`${String(i).padStart(4,'0')}.png`), canvas.toBuffer('image/png'));
    }
    console.log(`Rendered ${theme}-${state}: ${frames} upstream frames`);
  }
}
await mkdir(path.join(root,'assets/thinking-orbs'),{recursive:true});
await writeFile(path.join(root,'assets/thinking-orbs/LICENSE.txt'),await readFile(new URL('./node_modules/thinking-orbs/LICENSE',import.meta.url)));
await writeFile(path.join(root,'assets/thinking-orbs/provenance.json'),JSON.stringify({
  package:'thinking-orbs',version:'0.3.1',source:'https://github.com/Jakubantalik/thinking-orbs',
  commit:'de85557ca220332586d070d8788c0e1d6e877a0d',preset:64,pixels,fps,frames,
  engine:'thinking-orbs/engine',loop:'half-second crossfade to first upstream frame',license:'MIT',
},null,2)+'\n');
