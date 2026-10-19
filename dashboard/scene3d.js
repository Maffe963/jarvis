// The dashboard in 3D (Three.js): Jarvis, the user and the agents as a scene you can orbit, hover and click.
// index.html owns the data (agents, events); this file only draws it. Each theme builds its own world.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";

const FINISHED = ["done", "stopped", "failed"];
const ACTIVE = ["working", "starting", "needs you"];
const ease = k => (k < .5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2);
const easeBack = k => 1 + 2.7 * Math.pow(k - 1, 3) + 1.7 * Math.pow(k - 1, 2);

// ---------- small texture helpers ----------
const textureCache = {};
function emojiTexture(ch) {
  if (textureCache[ch]) return textureCache[ch];
  const c = document.createElement("canvas"); c.width = c.height = 128;
  const g = c.getContext("2d");
  g.font = '96px "Segoe UI Emoji","Apple Color Emoji","Noto Color Emoji",sans-serif';
  g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(ch, 64, 72);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
  return (textureCache[ch] = t);
}
function glowTexture() {
  if (textureCache.glow) return textureCache.glow;
  const c = document.createElement("canvas"); c.width = c.height = 64;
  const g = c.getContext("2d"), r = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  r.addColorStop(0, "rgba(255,255,255,1)"); r.addColorStop(.25, "rgba(255,255,255,.6)"); r.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = r; g.fillRect(0, 0, 64, 64);
  return (textureCache.glow = new THREE.CanvasTexture(c));
}
function planetTexture(hue, star) {   // banded gas-giant (or star surface) colours
  const key = `planet${hue.toFixed(3)}${star ? "s" : ""}`;
  if (textureCache[key]) return textureCache[key];
  const c = document.createElement("canvas"); c.width = 256; c.height = 128;
  const g = c.getContext("2d"), wobble = Math.random() * 10;
  for (let y = 0; y < 128; y++) {
    const band = Math.sin(y * .19 + wobble) * .5 + Math.sin(y * .053 + wobble * 2) * .5;
    g.fillStyle = star ? `hsl(${30 + band * 14}, 100%, ${58 + band * 12}%)` : `hsl(${hue * 360 + band * 22}, ${48 + band * 18}%, ${42 + band * 16}%)`;
    g.fillRect(0, y, 256, 1);
  }
  for (let i = 0; i < (star ? 160 : 30); i++) {   // storms or sunspots
    g.fillStyle = star ? `rgba(255,${200 + Math.random() * 55 | 0},120,.35)` : `hsla(${hue * 360 + 30}, 60%, 70%, .18)`;
    g.beginPath(); g.ellipse(Math.random() * 256, Math.random() * 128, 2 + Math.random() * 10, 1 + Math.random() * 3, 0, 0, 7); g.fill();
  }
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
  return (textureCache[key] = t);
}
function tiled(draw) {   // draw a pattern 9 times, offset by the canvas size, so it tiles without seams
  const c = document.createElement("canvas"); c.width = c.height = 512;
  const g = c.getContext("2d");
  for (const dx of [-512, 0, 512]) for (const dy of [-512, 0, 512]) { g.save(); g.translate(dx, dy); draw(g); g.restore(); }
  const t = new THREE.CanvasTexture(c); t.wrapS = t.wrapT = THREE.RepeatWrapping; t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
function seeded(seed) { return () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646; }
function crackTexture(key) {   // glowing lava cracks on black (the same cracks for every copy, so tiling works)
  if (textureCache["crack" + key]) return textureCache["crack" + key];
  const t = tiled(g => {
    const rnd = seeded(11);
    g.lineCap = "round"; g.shadowColor = "#ff7a1a"; g.shadowBlur = 8;
    for (let i = 0; i < 18; i++) {
      let x = rnd() * 512, y = rnd() * 512, a = rnd() * Math.PI * 2;
      g.strokeStyle = rnd() < .3 ? "#ffd84a" : "#ff6a1a"; g.lineWidth = 1 + rnd() * 2.5;
      g.beginPath(); g.moveTo(x, y);
      for (let j = 0; j < 9; j++) { a += (rnd() - .5) * 1.4; x += Math.cos(a) * 26; y += Math.sin(a) * 26; g.lineTo(x, y); }
      g.stroke();
    }
  });
  return (textureCache["crack" + key] = t);
}
function causticTexture() {   // wavy light lines like sunlight through water
  if (textureCache.caustic) return textureCache.caustic;
  const t = tiled(g => {
    const rnd = seeded(5);
    g.strokeStyle = "rgba(170,250,255,.6)"; g.lineWidth = 3;
    for (let i = 0; i < 40; i++) {
      let x = rnd() * 512, y = rnd() * 512; g.beginPath(); g.moveTo(x, y);
      for (let j = 0; j < 4; j++) { const cx = x + (rnd() - .5) * 120, cy = y + (rnd() - .5) * 120; x += (rnd() - .5) * 140; y += (rnd() - .5) * 140; g.quadraticCurveTo(cx, cy, x, y); }
      g.stroke();
    }
  });
  return (textureCache.caustic = t);
}
function auroraTexture() {   // a curtain of light: green at the bottom, violet at the top, streaked
  if (textureCache.aurora) return textureCache.aurora;
  const c = document.createElement("canvas"); c.width = 512; c.height = 128; const g = c.getContext("2d");
  const grad = g.createLinearGradient(0, 128, 0, 0);
  grad.addColorStop(0, "rgba(92,255,200,0)"); grad.addColorStop(.12, "rgba(92,255,200,.95)"); grad.addColorStop(.45, "rgba(60,210,255,.6)");
  grad.addColorStop(.8, "rgba(165,120,255,.35)"); grad.addColorStop(1, "rgba(165,120,255,0)");
  g.fillStyle = grad; g.fillRect(0, 0, 512, 128);
  g.globalCompositeOperation = "destination-out";
  for (let x = 0; x < 512; x += 2) { g.fillStyle = `rgba(0,0,0,${Math.random() * .7})`; g.fillRect(x, 0, 2, 128); }
  const t = new THREE.CanvasTexture(c); t.wrapS = THREE.RepeatWrapping; t.colorSpace = THREE.SRGBColorSpace;
  return (textureCache.aurora = t);
}
function risingPoints(count, spread, low, high, color, size) {   // bubbles, embers or snow
  const pts = [];
  for (let i = 0; i < count; i++) pts.push((Math.random() - .5) * spread, low + Math.random() * (high - low), (Math.random() - .5) * spread);
  const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
  const points = new THREE.Points(geo, new THREE.PointsMaterial({ color, size, transparent: true, opacity: .8, depthWrite: false, blending: THREE.AdditiveBlending }));
  points.userData.range = [low, high]; return points;
}
function moveParticles(points, t, dt, speed) {   // speed > 0 rises, < 0 falls; each wraps round
  const p = points.geometry.attributes.position, [low, high] = points.userData.range;
  for (let i = 0; i < p.count; i++) {
    let y = p.getY(i) + dt * speed * (.7 + (i % 7) * .1);
    if (y > high) y = low; if (y < low) y = high;
    p.setY(i, y); p.setX(i, p.getX(i) + Math.sin(t * 1.5 + i) * .003);
  }
  p.needsUpdate = true;
}
function palmTexture() {   // a palm tree silhouette with a neon rim, for the synthwave roadside
  if (textureCache.palm) return textureCache.palm;
  const c = document.createElement("canvas"); c.width = 256; c.height = 512; const g = c.getContext("2d");
  g.shadowColor = "#ff2bd6"; g.shadowBlur = 14; g.strokeStyle = "#1a0533"; g.lineCap = "round";
  for (let w = 16; w > 6; w -= 3) {   // the trunk, thick at the bottom
    g.lineWidth = w; g.beginPath(); g.moveTo(120, 510); g.quadraticCurveTo(100, 300, 150, 130); g.stroke();
  }
  g.lineWidth = 9;
  for (const [dx, dy, bend] of [[-110, 10, 50], [-90, 70, 40], [-40, 110, 20], [110, 20, -50], [95, 80, -40], [45, 115, -20], [0, -60, 30]]) {
    g.beginPath(); g.moveTo(150, 130); g.quadraticCurveTo(150 + dx / 2, 130 + dy / 2 - bend, 150 + dx, 130 + dy); g.stroke();
  }
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
  return (textureCache.palm = t);
}
function every(scene, key, seconds, t, run) {   // run something every so often (a little irregularly) from a theme's tick
  const next = (scene.userData.next ??= {});
  if (next[key] === undefined) next[key] = t + seconds * Math.random();
  if (t >= next[key]) { next[key] = t + seconds * (.6 + Math.random() * .8); run(); }
}
const sprite = (tex, size, opts = {}) => {
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, ...opts }));
  s.scale.set(size, size, 1); return s;
};
const mat = (color, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness: .8, metalness: .05, flatShading: true, ...extra });
function skyTexture(key, stops) {   // a vertical gradient for the background: [[0 (top) .. 1 (bottom), "#rrggbb"], ...]
  if (textureCache["sky" + key]) return textureCache["sky" + key];
  const c = document.createElement("canvas"); c.width = 4; c.height = 512; const g = c.getContext("2d");
  const grad = g.createLinearGradient(0, 0, 0, 512);
  for (const [at, color] of stops) grad.addColorStop(at, color);
  g.fillStyle = grad; g.fillRect(0, 0, 4, 512);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
  return (textureCache["sky" + key] = t);
}
function rakedSandTexture() {   // raked gravel: rings round the middle, with grain
  if (textureCache.raked) return textureCache.raked;
  const c = document.createElement("canvas"); c.width = c.height = 1024; const g = c.getContext("2d"), rnd = seeded(7);
  g.fillStyle = "#e6d9bf"; g.fillRect(0, 0, 1024, 1024);
  for (let r = 30; r < 520; r += 11) {
    g.lineWidth = 4; g.strokeStyle = "rgba(140,112,72,.22)"; g.beginPath(); g.arc(512, 512, r, 0, Math.PI * 2); g.stroke();
    g.lineWidth = 1.5; g.strokeStyle = "rgba(255,252,240,.55)"; g.beginPath(); g.arc(512, 512, r - 3, 0, Math.PI * 2); g.stroke();
  }
  for (let i = 0; i < 9000; i++) { g.fillStyle = rnd() < .5 ? "rgba(120,96,60,.18)" : "rgba(255,255,255,.3)"; g.fillRect(rnd() * 1024, rnd() * 1024, 2, 2); }
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4;
  return (textureCache.raked = t);
}
function petalTexture() {
  if (textureCache.petal) return textureCache.petal;
  const c = document.createElement("canvas"); c.width = c.height = 32; const g = c.getContext("2d");
  g.fillStyle = "#ffffff"; g.beginPath(); g.ellipse(16, 16, 13, 7, .6, 0, Math.PI * 2); g.fill();
  return (textureCache.petal = new THREE.CanvasTexture(c));
}
function ridgeTexture(key, layers) {   // misty mountain ridges, back to front: [[colour, base height 0..1, roughness], ...]
  if (textureCache["ridge" + key]) return textureCache["ridge" + key];
  const c = document.createElement("canvas"); c.width = 1024; c.height = 256; const g = c.getContext("2d"), rnd = seeded(3);
  for (const [color, base, rough] of layers) {
    const ph = [rnd() * 9, rnd() * 9, rnd() * 9];
    g.fillStyle = color; g.beginPath(); g.moveTo(0, 256);
    for (let x = 0; x <= 1024; x += 4) {
      const k = x / 1024, h = base + rough * (Math.sin(k * 7 + ph[0]) * .5 + Math.sin(k * 17 + ph[1]) * .3 + Math.abs(Math.sin(k * 3 + ph[2])) * .6);
      g.lineTo(x, 256 - h * 256);
    }
    g.lineTo(1024, 256); g.fill();
  }
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
  return (textureCache["ridge" + key] = t);
}
function plankTexture() {   // dark workshop floorboards with brass nails
  if (textureCache.planks) return textureCache.planks;
  const t = tiled(g => {
    const rnd = seeded(21);
    for (let row = 0; row < 8; row++) {
      let x = -rnd() * 300;
      while (x < 512) {
        const w = 160 + rnd() * 200, shade = 34 + rnd() * 18 | 0;
        g.fillStyle = `rgb(${shade + 26},${shade + 10},${shade - 8})`; g.fillRect(x, row * 64, w, 64);
        g.strokeStyle = "rgba(0,0,0,.18)"; g.lineWidth = 1;
        for (let k = 0; k < 6; k++) { const y = row * 64 + 6 + rnd() * 52; g.beginPath(); g.moveTo(x, y); g.bezierCurveTo(x + w * .3, y + 4, x + w * .6, y - 4, x + w, y + 1); g.stroke(); }
        g.fillStyle = "rgba(0,0,0,.6)"; g.fillRect(x, row * 64, 3, 64);
        g.fillStyle = "#c79b4a"; for (const yy of [12, 52]) { g.beginPath(); g.arc(x + 10, row * 64 + yy, 2.2, 0, 7); g.fill(); }
        x += w;
      }
      g.fillStyle = "rgba(0,0,0,.7)"; g.fillRect(0, row * 64, 512, 3);
    }
  });
  t.repeat.set(5, 5);
  return (textureCache.planks = t);
}
function solarTexture() {   // a solar panel's cells
  if (textureCache.solar) return textureCache.solar;
  const c = document.createElement("canvas"); c.width = 128; c.height = 80; const g = c.getContext("2d");
  g.fillStyle = "#c9ccd2"; g.fillRect(0, 0, 128, 80);
  for (let x = 0; x < 8; x++) for (let y = 0; y < 5; y++) {
    const grad = g.createLinearGradient(0, y * 16, 0, y * 16 + 16); grad.addColorStop(0, "#3a5ea8"); grad.addColorStop(1, "#13224a");
    g.fillStyle = grad; g.fillRect(x * 16 + 1, y * 16 + 1, 14, 14);
  }
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
  return (textureCache.solar = t);
}
function gearGeometry(r, teeth, depth) {   // a toothed wheel with spoke holes, centred on its axle (facing +z)
  const key = `gear${r}|${teeth}|${depth}`;
  if (textureCache[key]) return textureCache[key];
  const s = new THREE.Shape(), inner = r * .85, step = Math.PI * 2 / teeth;
  for (let i = 0; i < teeth; i++) {
    const a = i * step, at = (rad, k) => [Math.cos(a + step * k) * rad, Math.sin(a + step * k) * rad];
    [at(inner, 0), at(r, .18), at(r, .45), at(inner, .63)].forEach(([x, y], j) => (i === 0 && j === 0 ? s.moveTo(x, y) : s.lineTo(x, y)));
  }
  s.closePath();
  const axle = new THREE.Path(); axle.absarc(0, 0, r * .14, 0, Math.PI * 2, true); s.holes.push(axle);
  const spokes = teeth > 9 ? 5 : 0;
  for (let i = 0; i < spokes; i++) {
    const a = i / spokes * Math.PI * 2, h = new THREE.Path();
    h.absarc(Math.cos(a) * r * .5, Math.sin(a) * r * .5, r * .17, 0, Math.PI * 2, true); s.holes.push(h);
  }
  const geo = new THREE.ExtrudeGeometry(s, { depth, bevelEnabled: true, bevelThickness: depth * .15, bevelSize: Math.min(depth * .15, r * .02), bevelSegments: 1, curveSegments: 10 });
  geo.translate(0, 0, -depth / 2);
  return (textureCache[key] = geo);
}

// ---------- themes ----------
const THEMES = {
  command: {   // a tactical war table: lit from above, hex tiles, amber holograms on metal pedestals, a radar sweep
    background: 0x0b0e12, bloom: [0.4, 0.4, 0.6], linkY: -1.05, msg: 0xffe2a8, home: [[0, 9.5, 13], [0, -1, 1.2]],
    status: { starting: 0xffa630, working: 0xffa630, waiting: 0x8fa6bf, "needs you": 0xff4d4d, done: 0x7ed957, stopped: 0x48505a, failed: 0xff4d4d },
    world(scene) {
      scene.fog = new THREE.Fog(0x0b0e12, 22, 55);
      scene.add(new THREE.HemisphereLight(0xc8d6e8, 0x1a1208, .8));
      const lamp = new THREE.SpotLight(0xfff1dc, 1400, 60, .7, .55, 2); lamp.position.set(4, 18, 6); lamp.castShadow = true;
      lamp.shadow.mapSize.set(2048, 2048); scene.add(lamp, lamp.target);
      const glow = new THREE.PointLight(0xffa630, 25, 18); glow.position.set(0, 2, 0); scene.add(glow);
      // the table: hexagonal tiles, a few lit like map markers
      const spots = [];
      for (let q = -18; q <= 18; q++) for (let r = -18; r <= 18; r++) {
        const x = (q + r / 2) * .88, z = r * .76;
        if (Math.hypot(x, z) < 13.4) spots.push([x, z]);
      }
      const hex = new THREE.InstancedMesh(new THREE.CylinderGeometry(.49, .49, .5, 6), mat(0xffffff, { metalness: .55, roughness: .5 }), spots.length);
      const m4 = new THREE.Matrix4(), c = new THREE.Color();
      spots.forEach(([x, z], i) => {
        m4.makeTranslation(x, -1.82 + Math.random() * .05, z); hex.setMatrixAt(i, m4);
        hex.setColorAt(i, c.set(Math.random() < .04 ? 0x6a4512 : 0x1d242d).offsetHSL(0, 0, (Math.random() - .5) * .025));
      });
      hex.receiveShadow = true; scene.add(hex);
      const rim = new THREE.Mesh(new THREE.TorusGeometry(13.6, .14, 8, 120), new THREE.MeshStandardMaterial({ color: 0xffa630, emissive: 0xffa630, emissiveIntensity: .8 }));
      rim.rotation.x = Math.PI / 2; rim.position.y = -1.56; scene.add(rim);
      for (const radius of [4.5, 9, 12.5]) {   // range rings
        const ring = new THREE.Mesh(new THREE.RingGeometry(radius - .025, radius + .025, 128), new THREE.MeshBasicMaterial({ color: 0xffa630, transparent: true, opacity: .35 }));
        ring.rotation.x = -Math.PI / 2; ring.position.y = -1.55; scene.add(ring);
      }
      const sweep = new THREE.Mesh(new THREE.CircleGeometry(13.4, 48, 0, .55), new THREE.MeshBasicMaterial({ color: 0xffa630, transparent: true, opacity: .13, depthWrite: false, blending: THREE.AdditiveBlending }));
      sweep.rotation.x = -Math.PI / 2; sweep.position.y = -1.54; scene.add(sweep); scene.userData.sweep = sweep;
      const dust = risingPoints(280, 30, -1.4, 7, 0xffc070, .05); dust.material.opacity = .45; scene.add(dust); scene.userData.dust = dust;
    },
    tick(scene, t, dt, fx) {
      scene.userData.sweep.rotation.z -= dt * .8;
      moveParticles(scene.userData.dust, t, dt, .15);
      every(scene, "ping", 6, t, () => fx.ping(new THREE.Vector3(0, -1.5, 0), 0xffa630, 13));   // sonar from the command table
    },
    node(kind, color) {   // a metal hex pedestal with an amber trim and a hologram floating above it
      const g = new THREE.Group(), big = kind === "jarvis", r = big ? 1.25 : kind === "user" ? .7 : .8;
      const base = new THREE.Mesh(new THREE.CylinderGeometry(r, r * 1.1, .35, 6), mat(0x2b333e, { metalness: .75, roughness: .35 }));
      base.position.y = -1.38; base.castShadow = base.receiveShadow = true;
      const trim = new THREE.LineSegments(new THREE.EdgesGeometry(base.geometry), new THREE.LineBasicMaterial({ color }));
      trim.position.copy(base.position);
      const pad = new THREE.Mesh(new THREE.CircleGeometry(r * .82, 6), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .6 }));
      pad.rotation.x = -Math.PI / 2; pad.position.y = -1.19;
      const tall = big ? 2.8 : 1.8;
      const column = new THREE.Mesh(new THREE.CylinderGeometry(r * .8, r * .8, tall, 6, 1, true),
        new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .09, side: THREE.DoubleSide, depthWrite: false }));
      column.position.y = -1.19 + tall / 2;
      const holo = new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: .75, metalness: .3, roughness: .35, transparent: true, opacity: .92 });
      const icon = new THREE.Mesh(big ? new THREE.TorusKnotGeometry(.5, .14, 120, 14) : kind === "user" ? new THREE.DodecahedronGeometry(.42) : new THREE.ConeGeometry(.42, .85, 4), holo);
      if (kind === "agent") icon.rotation.x = Math.PI;   // a map pin pointing down at the table
      icon.position.y = big ? .55 : .15; icon.castShadow = true;
      g.add(base, trim, pad, column, icon);
      g.userData = { tint: [trim.material, pad.material, column.material, holo], spin: [[icon, 0, big ? .5 : .9, 0]], float: icon, height: big ? 2.6 : 1.7 };
      return g;
    },
    link: color => new THREE.LineDashedMaterial({ color, dashSize: .25, gapSize: .15, transparent: true, opacity: .7 }),
    particle(color) { return sprite(glowTexture(), .9, { color, blending: THREE.AdditiveBlending }); },
    burst(scene, at, fx) { fx.ring(at, 0x7ed957, 3); },
  },

  synthwave: {   // an 80s night drive: a scrolling neon grid, a striped sunset, wireframe mountains
    background: 0x0b0221, bloom: [1.0, 0.55, 0.12], msg: 0xff2bd6, home: [[0, 5.5, 15], [0, .3, -1]],
    status: { starting: 0x00f0ff, working: 0x00f0ff, waiting: 0xffb000, "needs you": 0xff2a6d, done: 0x05ffa1, stopped: 0x4a3a6a, failed: 0xff2a6d },
    world(scene) {
      scene.fog = new THREE.Fog(0x1a0433, 18, 80);
      scene.add(new THREE.AmbientLight(0xff66cc, .5));
      const floor = new THREE.Mesh(new THREE.PlaneGeometry(220, 220), new THREE.MeshBasicMaterial({ color: 0x0b0221 }));
      floor.rotation.x = -Math.PI / 2; floor.position.y = -1.63; scene.add(floor);
      const grid = new THREE.GridHelper(200, 100, 0xff2bd6, 0xa100ff); grid.position.y = -1.6; scene.add(grid); scene.userData.grid = grid;
      // the sun: a gradient disc with the classic gaps cut across its lower half
      const c = document.createElement("canvas"); c.width = c.height = 256; const g = c.getContext("2d");
      const grad = g.createLinearGradient(0, 0, 0, 256);
      grad.addColorStop(0, "#ffe83a"); grad.addColorStop(.55, "#ff8a3a"); grad.addColorStop(1, "#ff2bd6");
      g.fillStyle = grad; g.beginPath(); g.arc(128, 128, 126, 0, Math.PI * 2); g.fill();
      g.globalCompositeOperation = "destination-out";
      for (let i = 0; i < 8; i++) g.fillRect(0, 138 + i * 15, 256, 2 + i * 1.4);
      const sunTex = new THREE.CanvasTexture(c); sunTex.colorSpace = THREE.SRGBColorSpace;
      const sun = new THREE.Mesh(new THREE.PlaneGeometry(36, 36), new THREE.MeshBasicMaterial({ map: sunTex, color: 0xdddddd, transparent: true, fog: false }));
      sun.position.set(0, 7, -85); scene.add(sun);
      const haze = sprite(glowTexture(), 80, { color: 0xff2bd6, opacity: .3, fog: false, blending: THREE.AdditiveBlending });
      haze.position.set(0, 5, -88); scene.add(haze);
      for (const side of [-1, 1]) {   // mountain ranges either side of the sun, solid dark with a neon wireframe
        const geo = new THREE.PlaneGeometry(56, 30, 28, 14); geo.rotateX(-Math.PI / 2);
        const p = geo.attributes.position;
        for (let i = 0; i < p.count; i++) {
          const outward = (p.getX(i) * side + 28) / 56, depth = Math.sin((p.getZ(i) / 30 + .5) * Math.PI);
          p.setY(i, Math.max(0, (Math.random() * .55 + .45) * 13 * outward * outward * depth));
        }
        geo.computeVertexNormals();
        const solid = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: 0x12022e, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }));
        const wire = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: 0xff2bd6, wireframe: true, transparent: true, opacity: .55 }));
        for (const m of [solid, wire]) { m.position.set(side * 40, -1.6, -50); scene.add(m); }
      }
      const stars = new THREE.BufferGeometry(), pts = [];
      for (let i = 0; i < 700; i++) pts.push((Math.random() - .5) * 200, 10 + Math.random() * 60, -40 - Math.random() * 80);
      stars.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
      scene.add(new THREE.Points(stars, new THREE.PointsMaterial({ color: 0xffd1f5, size: .25, fog: false })));
      const palms = [];
      for (const side of [-1, 1]) for (let i = 0; i < 9; i++) {   // palm trees lining the road
        const palm = new THREE.Mesh(new THREE.PlaneGeometry(4.5, 9), new THREE.MeshBasicMaterial({ map: palmTexture(), transparent: true, depthWrite: false }));
        palm.position.set(side * (12 + (i % 3) * 1.6), 2.9, -64 + i * 9 + (side > 0 ? 4.5 : 0)); palm.scale.x = side * (.85 + (i % 2) * .3);
        scene.add(palm); palms.push(palm);
      }
      scene.userData.palms = palms;
    },
    tick(scene, t, dt, fx) {
      scene.userData.grid.position.z = (t * 4) % 2;   // the road rushing towards you
      for (const palm of scene.userData.palms) { palm.position.z += dt * 4; if (palm.position.z > 16) palm.position.z -= 81; }
      every(scene, "star", 3.5, t, () => {
        const x = (Math.random() - .5) * 90, from = new THREE.Vector3(x, 30 + Math.random() * 18, -70);
        fx.shootingStar(0xffd1f5, from, from.clone().add(new THREE.Vector3(Math.sign(-x || 1) * 22, -10, 0)), 1100);
      });
    },
    node(kind, color) {   // neon wireframe solids: a pyramid per agent, an icosahedron for Jarvis, a cube for you
      const g = new THREE.Group(), big = kind === "jarvis", r = big ? 1.4 : kind === "user" ? .75 : .9;
      const geo = big ? new THREE.IcosahedronGeometry(r, 0) : kind === "user" ? new THREE.BoxGeometry(r * 1.2, r * 1.2, r * 1.2) : new THREE.ConeGeometry(r, r * 1.7, 4);
      const body = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: 0x14032a, transparent: true, opacity: .85 }));
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({ color }));
      const shape = new THREE.Group(); shape.add(body, edges); g.add(shape);
      const halo = new THREE.Mesh(new THREE.RingGeometry(r * 1.1, r * 1.3, 48), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .8, side: THREE.DoubleSide }));
      halo.rotation.x = -Math.PI / 2; halo.position.y = -1.55; g.add(halo);
      const tint = [edges.material, halo.material], spin = [[shape, 0, .6, 0]];
      if (big) {
        const orbit = new THREE.Mesh(new THREE.TorusGeometry(r * 1.6, .035, 8, 96), new THREE.MeshBasicMaterial({ color: 0xff2bd6 }));
        orbit.rotation.x = Math.PI / 2.4; g.add(orbit); spin.push([orbit, 0, 0, .5]);
      }
      g.userData = { tint, spin, float: shape, height: r * 1.7 };
      return g;
    },
    link: color => new THREE.LineBasicMaterial({ color, transparent: true, opacity: .55 }),
    particle(color) { return sprite(glowTexture(), .9, { color, blending: THREE.AdditiveBlending }); },
    burst(scene, at, fx) { fx.ring(at, 0xff2bd6, 3.5); setTimeout(() => fx.ring(at, 0x00f0ff, 2.5), 160); },
  },

  galaxy: {   // deep space: Jarvis is a star, each agent a planet with its own moon, you're the space station
    background: 0x020108, bloom: [0.75, 0.5, 0.3], msg: 0xc9a0ff, home: [[0, 9, 14], [0, -.5, 1.2]],
    status: { starting: 0x7fd7ff, working: 0x7fd7ff, waiting: 0xc9a0ff, "needs you": 0xff5577, done: 0x8affc1, stopped: 0x3a3d5a, failed: 0xff5577 },
    world(scene) {
      scene.add(new THREE.AmbientLight(0x6670aa, .4));
      scene.add(new THREE.PointLight(0xffd9a0, 320, 0, 2));   // sunlight from Jarvis
      // a spiral galaxy turning slowly beneath everything
      const n = 9000, pos = new Float32Array(n * 3), col = new Float32Array(n * 3), c = new THREE.Color();
      for (let i = 0; i < n; i++) {
        const d = Math.pow(Math.random(), .7) * 32, a = (i % 3) / 3 * Math.PI * 2 + d * .32 + (Math.random() - .5) * .6;
        const spread = () => (Math.random() - .5) * (1.4 + d * .06);
        pos.set([Math.cos(a) * d + spread(), -4.5 - Math.random() * .8 * (1 - d / 32), Math.sin(a) * d + spread()], i * 3);
        c.setHSL((.62 + d / 32 * .3 + Math.random() * .05) % 1, .8, .5 + (1 - d / 32) * .35); col.set([c.r, c.g, c.b], i * 3);
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.BufferAttribute(pos, 3)); geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
      const galaxy = new THREE.Points(geo, new THREE.PointsMaterial({ size: .09, vertexColors: true, transparent: true, opacity: .9, blending: THREE.AdditiveBlending, depthWrite: false }));
      scene.add(galaxy); scene.userData.galaxy = galaxy;
      const bulge = sprite(glowTexture(), 16, { color: 0xffc9f0, opacity: .45, blending: THREE.AdditiveBlending }); bulge.position.y = -4.5; scene.add(bulge);
      const far = [];
      for (let i = 0; i < 2500; i++) { const v = new THREE.Vector3().randomDirection().multiplyScalar(90 + Math.random() * 60); far.push(v.x, v.y, v.z); }
      const farGeo = new THREE.BufferGeometry(); farGeo.setAttribute("position", new THREE.Float32BufferAttribute(far, 3));
      scene.add(new THREE.Points(farGeo, new THREE.PointsMaterial({ color: 0xffffff, size: .35 })));
      for (const [color, x, y, z] of [[0x7b2bff, -45, 12, -60], [0xff2b9d, 50, 6, -55], [0x2bb5ff, -5, 24, -85]]) {   // nebulae
        const s = sprite(glowTexture(), 70, { color, opacity: .2, blending: THREE.AdditiveBlending }); s.position.set(x, y, z); scene.add(s);
      }
      // an asteroid belt circling the whole system
      const rocks = new THREE.InstancedMesh(new THREE.DodecahedronGeometry(.16, 0), mat(0x9a8c84, { roughness: 1 }), 420);
      const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), e = new THREE.Euler(), s3 = new THREE.Vector3();
      for (let i = 0; i < 420; i++) {
        const a = Math.random() * Math.PI * 2, d = 13.5 + Math.random() * 3.2, k = .4 + Math.random() * 1.6;
        m4.compose(new THREE.Vector3(Math.cos(a) * d, -1.2 + (Math.random() - .5) * .9, Math.sin(a) * d),
                   q.setFromEuler(e.set(Math.random() * 3, Math.random() * 3, 0)), s3.set(k, k * (.6 + Math.random() * .6), k));
        rocks.setMatrixAt(i, m4);
      }
      const belt = new THREE.Group(); belt.add(rocks); scene.add(belt); scene.userData.belt = belt;
    },
    tick(scene, t, dt, fx) {
      scene.userData.galaxy.rotation.y += dt * .03;
      scene.userData.belt.rotation.y -= dt * .05;
      every(scene, "comet", 6, t, () => {
        const from = new THREE.Vector3().randomDirection().multiplyScalar(60).setY(10 + Math.random() * 25);
        fx.shootingStar(0xbfe6ff, from, from.clone().multiplyScalar(.35).setY(from.y - 12), 1600);
      });
    },
    node(kind, color, info) {
      const g = new THREE.Group(), add = THREE.AdditiveBlending;
      if (kind === "jarvis") {   // a star with a two-layer corona (always gold, whatever the status)
        const star = new THREE.Mesh(new THREE.SphereGeometry(1.2, 48, 32), new THREE.MeshBasicMaterial({ map: planetTexture(.09, true), color: 0xb0a090 }));
        g.add(star, sprite(glowTexture(), 4.2, { color: 0xffb347, opacity: .45, blending: add }), sprite(glowTexture(), 8, { color: 0xff7a2b, opacity: .14, blending: add }));
        g.userData = { tint: [], spin: [[star, 0, .15, 0]], float: null, height: 2.2 };
        return g;
      }
      if (kind === "user") {     // a ring station with solar panels and a beacon
        const hull = mat(0xc8d0dc, { metalness: .7, roughness: .35, flatShading: false });
        const station = new THREE.Group();
        const ring = new THREE.Mesh(new THREE.TorusGeometry(.75, .12, 12, 48), hull); ring.rotation.x = Math.PI / 2;
        station.add(ring, new THREE.Mesh(new THREE.CylinderGeometry(.18, .18, 1.2, 12), hull));
        for (const turn of [0, Math.PI / 2]) { const spoke = new THREE.Mesh(new THREE.BoxGeometry(1.5, .05, .05), hull); spoke.rotation.y = turn; station.add(spoke); }
        for (const x of [-1, 1]) {
          const panel = new THREE.Mesh(new THREE.BoxGeometry(.9, .02, .38), mat(0x2a4dbf, { metalness: .5, roughness: .3 })); panel.position.x = x * 1.4; station.add(panel);
        }
        const beacon = sprite(glowTexture(), .7, { color, blending: add }); beacon.position.y = .75; station.add(beacon);
        station.rotation.z = .25; g.add(station);
        g.userData = { tint: [beacon.material], spin: [[station, 0, .3, 0]], float: station, height: 1.6 };
        return g;
      }
      // a planet in its own colours, with an atmosphere and a ring in the status colour, and a moon going round
      const hue = [...String(info.id || info.name || "x")].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) % 997, 7) / 997;
      const planet = new THREE.Mesh(new THREE.SphereGeometry(.75, 40, 28), new THREE.MeshStandardMaterial({ map: planetTexture(hue), roughness: .9 }));
      planet.rotation.z = .3;
      const atmo = sprite(glowTexture(), 2.3, { color, opacity: .35, blending: add });
      const ring = new THREE.Mesh(new THREE.RingGeometry(1.0, 1.35, 64), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .55, side: THREE.DoubleSide }));
      ring.rotation.x = Math.PI / 2 - .45;
      const pivot = new THREE.Group(), moon = new THREE.Mesh(new THREE.SphereGeometry(.13, 16, 12), mat(0xcfd3dc, { flatShading: false }));
      moon.position.x = 1.75; pivot.add(moon); pivot.rotation.z = .3;
      g.add(atmo, planet, ring, pivot);
      g.userData = { tint: [atmo.material, ring.material], spin: [[planet, 0, .4, 0], [pivot, 0, 1.1, 0]], float: null, height: 1.9 };
      return g;
    },
    link: color => new THREE.LineDashedMaterial({ color, dashSize: .15, gapSize: .25, transparent: true, opacity: .4 }),
    particle(color) { return sprite(glowTexture(), .85, { color, blending: THREE.AdditiveBlending }); },   // a comet
    burst(scene, at, fx) { fx.ring(at, 0x8affc1, 3); fx.spawn(at.clone().setY(at.y - 1), 0x8affc1); },
  },

  matrix: {   // inside the mainframe: digital rain on the walls, server monoliths, a spinning core
    background: 0x000700, bloom: [0.75, 0.45, 0.2], msg: 0xb6ffcc, home: [[0, 8.5, 13.5], [0, -.3, 1.2]],
    status: { starting: 0x00ff66, working: 0x00ff66, waiting: 0xd4ff00, "needs you": 0xff3355, done: 0x66ffcc, stopped: 0x1f4a2a, failed: 0xff3355 },
    world(scene) {
      scene.fog = new THREE.FogExp2(0x000700, .028);
      scene.add(new THREE.AmbientLight(0x2a6a3a, .8));
      const key = new THREE.PointLight(0x00ff66, 50, 40); key.position.set(0, 7, 3); scene.add(key);
      const grid = new THREE.GridHelper(80, 80, 0x00ff66, 0x0b3318); grid.position.y = -1.6; scene.add(grid);
      // the rain is drawn on a canvas and wrapped round the inside of a cylinder
      const c = document.createElement("canvas"); c.width = c.height = 512;
      const g = c.getContext("2d"); g.fillStyle = "#000"; g.fillRect(0, 0, 512, 512);
      const tex = new THREE.CanvasTexture(c); tex.wrapS = THREE.RepeatWrapping; tex.repeat.set(5, 1); tex.colorSpace = THREE.SRGBColorSpace;
      const wall = new THREE.Mesh(new THREE.CylinderGeometry(26, 26, 34, 64, 1, true),
        new THREE.MeshBasicMaterial({ map: tex, side: THREE.BackSide, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, fog: false }));
      wall.position.y = 10; scene.add(wall);
      scene.userData.rain = { g, tex, drops: Array.from({ length: 32 }, () => Math.random() * -30), wait: 0 };
      const bits = new THREE.BufferGeometry(), pts = [];
      for (let i = 0; i < 600; i++) pts.push((Math.random() - .5) * 40, Math.random() * 14 - 1.5, (Math.random() - .5) * 40);
      bits.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
      const motes = new THREE.Points(bits, new THREE.PointsMaterial({ color: 0x00ff66, size: .06, transparent: true, opacity: .7 }));
      motes.userData.drift = true; scene.add(motes);
      // data packets racing along the floor grid towards the core
      const packets = new THREE.InstancedMesh(new THREE.BoxGeometry(.1, .1, .45), new THREE.MeshBasicMaterial({ color: 0x7dffaa }), 60);
      scene.add(packets);
      scene.userData.packets = { mesh: packets, list: Array.from({ length: 60 }, () => ({
        axis: Math.random() < .5 ? "x" : "z", lane: Math.round((Math.random() - .5) * 24), at: 6 + Math.random() * 16,
        dir: Math.random() < .5 ? 1 : -1, speed: 3 + Math.random() * 5 })) };
    },
    tick(scene, t, dt) {
      const packets = scene.userData.packets, m4 = new THREE.Matrix4(), turn = new THREE.Matrix4().makeRotationY(Math.PI / 2);
      packets.list.forEach((p, i) => {
        p.at -= p.speed * dt; if (p.at < 1.5) { p.at = 14 + Math.random() * 8; p.lane = Math.round((Math.random() - .5) * 24); }
        const along = p.at * p.dir;
        m4.makeTranslation(p.axis === "x" ? along : p.lane, -1.55, p.axis === "x" ? p.lane : along);
        if (p.axis === "x") m4.multiply(turn);
        packets.mesh.setMatrixAt(i, m4);
      });
      packets.mesh.instanceMatrix.needsUpdate = true;
      const rain = scene.userData.rain; rain.wait -= dt; if (rain.wait > 0) return; rain.wait = .05;
      const g = rain.g; g.fillStyle = "rgba(0,7,0,.13)"; g.fillRect(0, 0, 512, 512);
      g.font = "bold 15px monospace";
      rain.drops.forEach((y, i) => {
        g.fillStyle = Math.random() < .1 ? "#e6ffee" : "#00ff66";
        g.fillText(String.fromCharCode(0x30a0 + (Math.random() * 96 | 0)), i * 16, y * 16);
        rain.drops[i] = y > 32 + Math.random() * 25 ? 0 : y + 1;
      });
      rain.tex.needsUpdate = true;
    },
    node(kind, color) {
      const g = new THREE.Group();
      if (kind === "jarvis") {   // the core: a glowing cube inside spinning wireframes, on a pillar of light
        const cube = new THREE.Mesh(new THREE.BoxGeometry(1.1, 1.1, 1.1), new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: .6, transparent: true, opacity: .9 }));
        const wire = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.OctahedronGeometry(1.8)), new THREE.LineBasicMaterial({ color }));
        const outer = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(2.3, 0)), new THREE.LineBasicMaterial({ color, transparent: true, opacity: .35 }));
        const core = new THREE.Group(); core.add(cube, wire, outer); core.position.y = .4; g.add(core);
        const beam = new THREE.Mesh(new THREE.CylinderGeometry(.06, .06, 30, 8), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .25 }));
        beam.position.y = 13.4; g.add(beam);
        g.userData = { tint: [cube.material, wire.material, outer.material, beam.material], spin: [[cube, .5, .7, 0], [wire, 0, -.4, 0], [outer, .2, .2, 0]], float: core, height: 3 };
        return g;
      }
      if (kind === "user") {     // a floating terminal screen
        const screen = new THREE.Mesh(new THREE.BoxGeometry(1.5, 1, .08), new THREE.MeshStandardMaterial({ color: 0x021a0a, metalness: .6, roughness: .3 }));
        const glass = new THREE.Mesh(new THREE.PlaneGeometry(1.3, .8), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .35 }));
        glass.position.z = .05;
        const edges = new THREE.LineSegments(new THREE.EdgesGeometry(screen.geometry), new THREE.LineBasicMaterial({ color }));
        const unit = new THREE.Group(); unit.add(screen, glass, edges); g.add(unit);
        g.userData = { tint: [glass.material, edges.material], spin: [[unit, 0, .4, 0]], float: unit, height: 1.4 };
        return g;
      }
      // a server monolith: dark slab, glowing edges, blinking lights and a scan ring running up it
      const slab = new THREE.Mesh(new THREE.BoxGeometry(.9, 2.2, .9), new THREE.MeshStandardMaterial({ color: 0x031a0a, metalness: .7, roughness: .3 }));
      slab.position.y = -.5;
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(slab.geometry), new THREE.LineBasicMaterial({ color })); edges.position.copy(slab.position);
      const lights = [];
      for (let i = 0; i < 6; i++) for (const z of [.46, -.46]) {
        const led = new THREE.Mesh(new THREE.BoxGeometry(.55, .06, .02), new THREE.MeshBasicMaterial({ color, transparent: true }));
        led.position.set(0, -1.3 + i * .3, z); g.add(led); lights.push(led.material);
      }
      const scan = new THREE.Mesh(new THREE.TorusGeometry(.8, .025, 6, 64), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .9 }));
      scan.rotation.x = Math.PI / 2; g.add(slab, edges, scan);
      g.userData = { tint: [edges.material, scan.material, ...lights], spin: [], float: null, height: 1.6, scan, blink: lights };
      return g;
    },
    link: color => new THREE.LineDashedMaterial({ color, dashSize: .1, gapSize: .1, transparent: true, opacity: .6 }),
    particle(color) { return sprite(glowTexture(), .8, { color, blending: THREE.AdditiveBlending }); },
    burst(scene, at, fx) { fx.ring(at, 0x66ffcc, 3); setTimeout(() => fx.ring(at, 0x00ff66, 2), 150); },
  },

  abyss: {   // the deep sea: glowing jellyfish, light shafts from far above, kelp, bubbles, and you in a yellow submarine
    background: 0x00121a, bloom: [0.9, 0.5, 0.15], msg: 0x9ff8ff, home: [[0, 7, 14], [0, -.2, 1.2]],
    status: { starting: 0x3dfcff, working: 0x3dfcff, waiting: 0x8a7dff, "needs you": 0xff4fa3, done: 0x7dffb2, stopped: 0x1c3a44, failed: 0xff4fa3 },
    world(scene) {
      scene.fog = new THREE.FogExp2(0x00141f, .045);
      scene.add(new THREE.HemisphereLight(0x3aa0c0, 0x001018, 1));
      const sun = new THREE.DirectionalLight(0x7fe0ff, .8); sun.position.set(2, 20, 4); scene.add(sun);
      const bed = new THREE.PlaneGeometry(120, 120, 80, 80); bed.rotateX(-Math.PI / 2);
      const p = bed.attributes.position;
      for (let i = 0; i < p.count; i++) p.setY(i, Math.sin(p.getX(i) * .25) * .15 + Math.cos(p.getZ(i) * .2 + p.getX(i) * .1) * .2);
      bed.computeVertexNormals();
      const floor = new THREE.Mesh(bed, new THREE.MeshStandardMaterial({ color: 0x1a4c4a, roughness: 1 })); floor.position.y = -1.95; scene.add(floor);
      const tex = causticTexture(); tex.repeat.set(10, 10);
      const caustics = new THREE.Mesh(new THREE.PlaneGeometry(70, 70), new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: .2, blending: THREE.AdditiveBlending, depthWrite: false }));
      caustics.rotation.x = -Math.PI / 2; caustics.position.y = -1.55; scene.add(caustics);
      const shafts = [];
      for (let i = 0; i < 7; i++) {   // light from the surface
        const shaft = new THREE.Mesh(new THREE.CylinderGeometry(.6 + Math.random(), 2.5 + Math.random() * 2, 26, 16, 1, true),
          new THREE.MeshBasicMaterial({ color: 0x6fe9ff, transparent: true, opacity: .05, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide, fog: false }));
        shaft.position.set((Math.random() - .5) * 34, 11, (Math.random() - .5) * 22 - 6); shaft.rotation.z = (Math.random() - .5) * .3;
        shaft.userData.phase = Math.random() * 6; scene.add(shaft); shafts.push(shaft);
      }
      const kelp = [];
      for (let i = 0; i < 34; i++) {
        const a = Math.random() * Math.PI * 2, d = 10 + Math.random() * 9, h = 3 + Math.random() * 5;
        const pts = Array.from({ length: 6 }, (_, j) => new THREE.Vector3(Math.sin(j * 1.3 + i) * .25, j / 5 * h, Math.cos(j * .9 + i) * .2));
        const strand = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 16, .07, 5), mat(0x1f7a4a, { emissive: 0x0a3a20 }));
        strand.position.set(Math.cos(a) * d, -1.9, Math.sin(a) * d); strand.userData.phase = Math.random() * 6; scene.add(strand); kelp.push(strand);
      }
      for (let i = 0; i < 16; i++) {
        const a = Math.random() * Math.PI * 2, d = 9 + Math.random() * 8;
        const rock = new THREE.Mesh(new THREE.DodecahedronGeometry(.4 + Math.random() * .9, 0), mat(0x22343c));
        rock.position.set(Math.cos(a) * d, -1.8, Math.sin(a) * d); rock.rotation.set(Math.random(), Math.random(), 0); scene.add(rock);
      }
      const bubbles = risingPoints(500, 40, -1.8, 12, 0xbff8ff, .09); scene.add(bubbles);
      const fishGeo = new THREE.ConeGeometry(.07, .32, 5); fishGeo.rotateZ(-Math.PI / 2);   // pointing along +x
      const fish = new THREE.InstancedMesh(fishGeo, new THREE.MeshBasicMaterial({ color: 0x9ff8ff, transparent: true, opacity: .85 }), 90);
      const school = Array.from({ length: 90 }, () => ({ a: Math.random() * Math.PI * 2, r: 10.5 + (Math.random() - .5) * 2.4,
                                                         y: 2.4 + (Math.random() - .5) * 1.6, wobble: Math.random() * 6 }));
      scene.add(fish);
      scene.userData.abyss = { tex, shafts, kelp, bubbles, fish, school };
    },
    tick(scene, t, dt) {
      const sea = scene.userData.abyss;
      sea.tex.offset.set(Math.sin(t * .1) * .3, t * .02);
      for (const shaft of sea.shafts) shaft.material.opacity = .035 + Math.sin(t * .6 + shaft.userData.phase) * .02;
      for (const k of sea.kelp) { k.rotation.z = Math.sin(t * .8 + k.userData.phase) * .08; k.rotation.x = Math.cos(t * .6 + k.userData.phase) * .06; }
      moveParticles(sea.bubbles, t, dt, .6);
      const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), up = new THREE.Vector3(0, 1, 0), one = new THREE.Vector3(1, 1, 1);
      sea.school.forEach((f, i) => {   // the school circles slowly, each fish weaving a little
        const a = f.a + t * .18, r = f.r + Math.sin(t * .9 + f.wobble) * .3;
        const at = new THREE.Vector3(Math.cos(a) * r, f.y + Math.sin(t * 1.3 + f.wobble) * .25, Math.sin(a) * r);
        m4.compose(at, q.setFromAxisAngle(up, -a - Math.PI / 2), one); sea.fish.setMatrixAt(i, m4);
      });
      sea.fish.instanceMatrix.needsUpdate = true;
    },
    node(kind, color) {
      const g = new THREE.Group(), add = THREE.AdditiveBlending;
      if (kind === "user") {   // a little yellow submarine with portholes and a headlight
        const hull = mat(0xffc42e, { metalness: .3, roughness: .45, flatShading: false }), sub = new THREE.Group();
        const body = new THREE.Mesh(new THREE.CapsuleGeometry(.42, 1.1, 8, 16), hull); body.rotation.z = Math.PI / 2;
        const tower = new THREE.Mesh(new THREE.BoxGeometry(.4, .35, .3), hull); tower.position.set(-.1, .5, 0);
        const scope = new THREE.Mesh(new THREE.CylinderGeometry(.03, .03, .35), mat(0x555555)); scope.position.set(-.05, .8, 0);
        const prop = new THREE.Mesh(new THREE.BoxGeometry(.04, .55, .12), mat(0x8a6a1a)); prop.position.x = -1.02;
        const lamp = sprite(glowTexture(), 1.3, { color, blending: add }); lamp.position.x = 1;
        sub.add(body, tower, scope, prop, lamp);
        for (const z of [-.43, .43]) for (const x of [-.35, 0, .35]) {
          const port = new THREE.Mesh(new THREE.CircleGeometry(.1, 16), new THREE.MeshBasicMaterial({ color: 0x9ff8ff, side: THREE.DoubleSide }));
          port.position.set(x, .05, z); sub.add(port);
        }
        sub.rotation.y = -Math.PI / 2; g.add(sub);   // facing Jarvis
        g.userData = { tint: [lamp.material], spin: [[prop, 5, 0, 0]], float: sub, height: 1.4 };
        return g;
      }
      // a jellyfish: a see-through glowing bell that pulses, a bright heart, trailing tentacles
      const big = kind === "jarvis", r = big ? 1.3 : .75, count = big ? 14 : 9, jelly = new THREE.Group();
      const bell = new THREE.Mesh(new THREE.SphereGeometry(r, 32, 16, 0, Math.PI * 2, 0, Math.PI / 2),
        new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: .55, transparent: true, opacity: .55, side: THREE.DoubleSide, roughness: .2 }));
      const heart = new THREE.Mesh(new THREE.SphereGeometry(r * .4, 16, 12), new THREE.MeshBasicMaterial({ color })); heart.position.y = r * .25;
      const rim = new THREE.Mesh(new THREE.TorusGeometry(r, .03, 6, 48), new THREE.MeshBasicMaterial({ color })); rim.rotation.x = Math.PI / 2;
      const strands = new THREE.LineBasicMaterial({ color, transparent: true, opacity: .7 }), tentacles = new THREE.Group();
      for (let i = 0; i < count; i++) {
        const a = i / count * Math.PI * 2, rr = r * (.4 + (i % 3) * .2), len = r * (1.8 + Math.random() * 1.2);
        const pts = Array.from({ length: 12 }, (_, j) => new THREE.Vector3(Math.cos(a) * rr + Math.sin(j * .8 + i) * .08, -j / 11 * len, Math.sin(a) * rr + Math.cos(j * .7 + i) * .08));
        tentacles.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), strands));
      }
      const glow = sprite(glowTexture(), r * 3.2, { color, opacity: .35, blending: add });
      jelly.add(bell, heart, rim, tentacles, glow); jelly.position.y = big ? .6 : .4; g.add(jelly);
      g.userData = { tint: [bell.material, heart.material, rim.material, strands, glow.material], spin: [[tentacles, 0, .25, 0]], float: jelly, pulse: bell, height: big ? 2.6 : 1.8 };
      return g;
    },
    link: color => new THREE.LineBasicMaterial({ color, transparent: true, opacity: .35 }),
    particle(color) { return sprite(glowTexture(), .8, { color, blending: THREE.AdditiveBlending }); },
    burst(scene, at, fx) { fx.ring(at, 0x7dffb2, 3); fx.spawn(at, 0x9ff8ff); },
  },

  forge: {   // a dragon's forge: dark rock split by glowing lava, a volcano, embers drifting up
    background: 0x120604, bloom: [0.8, 0.5, 0.35], msg: 0xffb347, home: [[0, 8, 13.5], [0, -.5, 1.2]],
    status: { starting: 0xffb347, working: 0xff7a1a, waiting: 0x9a8f88, "needs you": 0xff2a2a, done: 0xffd84a, stopped: 0x3a2a26, failed: 0xff2a2a },
    world(scene) {
      scene.fog = new THREE.Fog(0x1a0805, 16, 60);
      scene.add(new THREE.HemisphereLight(0xff9a6a, 0x1a0604, .6));
      const heat = new THREE.PointLight(0xff5a1a, 60, 30); heat.position.set(0, -.5, 0); scene.add(heat);
      const moon = new THREE.DirectionalLight(0xffc0a0, .7); moon.position.set(-6, 14, 8); moon.castShadow = true;
      moon.shadow.mapSize.set(2048, 2048); Object.assign(moon.shadow.camera, { left: -16, right: 16, top: 16, bottom: -16 }); scene.add(moon);
      const cracks = crackTexture("ground"); cracks.repeat.set(5, 5);
      const ground = new THREE.Mesh(new THREE.PlaneGeometry(90, 90), new THREE.MeshStandardMaterial({ color: 0x1e1512, roughness: .9, emissive: 0xff5a00, emissiveMap: cracks, emissiveIntensity: 1.2 }));
      ground.rotation.x = -Math.PI / 2; ground.position.y = -1.6; ground.receiveShadow = true; scene.add(ground);
      const volcanoGeo = new THREE.CylinderGeometry(3, 22, 20, 28, 6), vp = volcanoGeo.attributes.position;
      for (let i = 0; i < vp.count; i++) if (vp.getY(i) < 9.9) { vp.setX(i, vp.getX(i) * (.9 + Math.random() * .2)); vp.setZ(i, vp.getZ(i) * (.9 + Math.random() * .2)); }
      volcanoGeo.computeVertexNormals();
      const volcano = new THREE.Mesh(volcanoGeo, mat(0x241a17)); volcano.position.set(-14, 8.4, -48); scene.add(volcano);
      const crater = sprite(glowTexture(), 18, { color: 0xff5a1a, opacity: .8, blending: THREE.AdditiveBlending, fog: false }); crater.position.set(-14, 18.6, -48); scene.add(crater);
      const flow = new THREE.CatmullRomCurve3([[-14, 18.2, -46], [-12.5, 13, -40], [-11, 7, -35], [-9.5, -.6, -29]].map(v => new THREE.Vector3(...v)));
      scene.add(new THREE.Mesh(new THREE.TubeGeometry(flow, 40, .5, 6), new THREE.MeshBasicMaterial({ color: 0xff6a1a })));
      for (let i = 0; i < 16; i++) {   // rock spires
        const a = Math.random() * Math.PI * 2, d = 12 + Math.random() * 9, h = 3 + Math.random() * 5;
        const spire = new THREE.Mesh(new THREE.ConeGeometry(.6 + Math.random() * .9, h, 5), mat(0x2a201c));
        spire.position.set(Math.cos(a) * d, -1.6 + h / 2, Math.sin(a) * d); spire.castShadow = true; scene.add(spire);
      }
      const embers = risingPoints(600, 40, -1.5, 13, 0xff8a2a, .1); scene.add(embers);
      scene.userData.forge = { ground, crater, embers };
    },
    tick(scene, t, dt, fx) {
      const f = scene.userData.forge;
      f.ground.material.emissiveIntensity = .6 + Math.sin(t * 1.3) * .18;
      f.crater.material.opacity = .65 + Math.sin(t * 2.3) * .15;
      moveParticles(f.embers, t, dt, .9);
      every(scene, "geyser", 3.5, t, () => {   // lava bursting up through the cracks
        const a = Math.random() * Math.PI * 2, d = 9 + Math.random() * 5;
        fx.fountain(new THREE.Vector3(Math.cos(a) * d, -1.5, Math.sin(a) * d), [0xff7a1a, 0xffd84a, 0xff3a1a], 34, 5.5);
      });
    },
    node(kind, color) {
      const g = new THREE.Group(), add = THREE.AdditiveBlending, stone = mat(0x3a302c, { roughness: .95 });
      if (kind === "jarvis") {   // a dragon egg glowing through its cracks, on a stone nest, with rocks circling it
        const shell = new THREE.MeshStandardMaterial({ color: 0x2a1410, roughness: .5, metalness: .2, emissive: color, emissiveMap: crackTexture("egg"), emissiveIntensity: 1.5 });
        shell.userData.keepColor = true;
        const egg = new THREE.Mesh(new THREE.SphereGeometry(1, 40, 28), shell); egg.scale.set(1, 1.35, 1); egg.position.y = .5;
        const nest = new THREE.Mesh(new THREE.TorusGeometry(1.05, .35, 8, 20), stone); nest.rotation.x = Math.PI / 2; nest.position.y = -.7;
        const pedestal = new THREE.Mesh(new THREE.CylinderGeometry(1.6, 1.9, .9, 7), stone); pedestal.position.y = -1.15;
        const orbit = new THREE.Group();
        for (let i = 0; i < 7; i++) {
          const rock = new THREE.Mesh(new THREE.DodecahedronGeometry(.18 + Math.random() * .12, 0), mat(0x4a3a32));
          const a = i / 7 * Math.PI * 2; rock.position.set(Math.cos(a) * 2.2, .3 + Math.sin(i * 2) * .4, Math.sin(a) * 2.2); orbit.add(rock);
        }
        const halo = sprite(glowTexture(), 5, { color, opacity: .35, blending: add }); halo.position.y = .5;
        g.add(pedestal, nest, egg, orbit, halo);
        g.traverse(o => { if (o.isMesh) o.castShadow = o.receiveShadow = true; });
        g.userData = { tint: [shell, halo.material], spin: [[orbit, 0, .35, 0], [egg, 0, .1, 0]], float: null, height: 2.8 };
        return g;
      }
      if (kind === "user") {     // a sword in the stone
        const rock = new THREE.Mesh(new THREE.DodecahedronGeometry(.8, 0), stone); rock.scale.set(1.2, .7, 1); rock.position.y = -1.1;
        const steel = new THREE.MeshStandardMaterial({ color: 0xd8d8e0, metalness: .9, roughness: .2, emissive: color, emissiveIntensity: .35 });
        steel.userData.keepColor = true;
        const blade = new THREE.Mesh(new THREE.BoxGeometry(.14, 1.6, .04), steel); blade.position.y = .05;
        const gold = mat(0xc9a03a, { metalness: .8, roughness: .3 });
        const guard = new THREE.Mesh(new THREE.BoxGeometry(.7, .1, .12), gold); guard.position.y = .9;
        const grip = new THREE.Mesh(new THREE.CylinderGeometry(.05, .05, .4, 8), mat(0x4a2a10)); grip.position.y = 1.15;
        const pommel = new THREE.Mesh(new THREE.SphereGeometry(.08, 10, 8), gold); pommel.position.y = 1.38;
        const halo = sprite(glowTexture(), 1.6, { color, opacity: .4, blending: add }); halo.position.y = .3;
        g.add(rock, blade, guard, grip, pommel, halo);
        g.traverse(o => { if (o.isMesh) o.castShadow = true; });
        g.userData = { tint: [steel, halo.material], spin: [], float: null, height: 1.9 };
        return g;
      }
      // a smith's brazier: an iron bowl of coals with flames that flicker harder while it works
      const iron = new THREE.MeshStandardMaterial({ color: 0x2b2420, metalness: .8, roughness: .4, flatShading: true });
      const stand = new THREE.Mesh(new THREE.CylinderGeometry(.12, .35, 1.1, 6), iron); stand.position.y = -1.05;
      const bowl = new THREE.Mesh(new THREE.CylinderGeometry(.75, .45, .5, 8), iron); bowl.position.y = -.3;
      const coals = new THREE.Mesh(new THREE.CircleGeometry(.68, 16), new THREE.MeshBasicMaterial({ color })); coals.rotation.x = -Math.PI / 2; coals.position.y = -.04;
      const fire = [[1.7, 0], [1.15, .4], [.7, .8]].map(([size, y]) => {
        const flame = sprite(glowTexture(), size, { color, blending: add, opacity: .85 }); flame.position.y = .15 + y; flame.userData.size = size; return flame;
      });
      const light = new THREE.PointLight(color, 6, 6); light.position.y = .6;
      g.add(stand, bowl, coals, ...fire, light);
      g.traverse(o => { if (o.isMesh) o.castShadow = true; });
      g.userData = { tint: [coals.material, ...fire.map(f => f.material), light], spin: [], float: null, height: 1.6, fire };
      return g;
    },
    link: color => new THREE.LineBasicMaterial({ color, transparent: true, opacity: .5 }),
    particle(color) { return sprite(glowTexture(), 1, { color, blending: THREE.AdditiveBlending }); },   // a fireball
    burst(scene, at, fx) { fx.ring(at, 0xffd84a, 3); fx.spawn(at, 0xffb347); },
  },

  aurora: {   // a frozen night: snow, ice crystals, pines, and the northern lights rippling across the sky
    background: 0x040a1c, bloom: [0.7, 0.5, 0.75], msg: 0xbfe9ff, home: [[0, 5.5, 15], [0, .8, -1]],
    status: { starting: 0x5cffc8, working: 0x5cffc8, waiting: 0xa58cff, "needs you": 0xff5c8a, done: 0xfff1a8, stopped: 0x3a4660, failed: 0xff5c8a },
    world(scene) {
      scene.fog = new THREE.Fog(0x0a1530, 25, 85);
      scene.add(new THREE.HemisphereLight(0x9fc6ff, 0x203050, .55));
      const moon = new THREE.DirectionalLight(0xcfe0ff, .8); moon.position.set(-8, 16, -6); moon.castShadow = true;
      moon.shadow.mapSize.set(2048, 2048); Object.assign(moon.shadow.camera, { left: -18, right: 18, top: 18, bottom: -18 }); scene.add(moon);
      const geo = new THREE.PlaneGeometry(140, 140, 70, 70); geo.rotateX(-Math.PI / 2);
      const p = geo.attributes.position;
      for (let i = 0; i < p.count; i++) {
        const x = p.getX(i), z = p.getZ(i), far = Math.max(0, Math.hypot(x, z) - 12);
        p.setY(i, Math.sin(x * .15) * .2 + Math.cos(z * .12) * .2 + far * .12 * (Math.sin(x * .3) * .5 + .7));
      }
      geo.computeVertexNormals();
      const snowfield = new THREE.Mesh(geo, mat(0xaebfd8, { roughness: .9 })); snowfield.position.y = -1.95; snowfield.receiveShadow = true; scene.add(snowfield);
      for (let i = 0; i < 40; i++) {   // snowy pines
        const a = Math.random() * Math.PI * 2, d = 13 + Math.random() * 16, tree = new THREE.Group(), s = .8 + Math.random() * .6;
        const trunk = new THREE.Mesh(new THREE.CylinderGeometry(.12, .16, .6, 6), mat(0x4a3022)); trunk.position.y = .3; tree.add(trunk);
        for (let j = 0; j < 3; j++) {
          const tier = new THREE.Mesh(new THREE.ConeGeometry(1 - j * .25, 1.1, 7), mat(0x1f4a3a)); tier.position.y = .9 + j * .6; tree.add(tier);
          const cap = new THREE.Mesh(new THREE.ConeGeometry((1 - j * .25) * .55, .45, 7), mat(0xf2f7ff)); cap.position.y = 1.25 + j * .6; tree.add(cap);
        }
        tree.scale.setScalar(s); tree.position.set(Math.cos(a) * d, -1.9 + Math.max(0, d - 12) * .06, Math.sin(a) * d);
        tree.traverse(o => { if (o.isMesh) o.castShadow = true; }); scene.add(tree);
      }
      for (let i = 0; i < 18; i++) {   // ice crystals poking out of the snow
        const a = Math.random() * Math.PI * 2, d = 9 + Math.random() * 6, h = .8 + Math.random() * 1.8;
        const crystal = new THREE.Mesh(new THREE.OctahedronGeometry(1, 0), new THREE.MeshStandardMaterial({ color: 0xbfe9ff, transparent: true, opacity: .75, roughness: .1, emissive: 0x1a4a6a }));
        crystal.scale.set(.3, h, .3); crystal.position.set(Math.cos(a) * d, -1.8 + h * .4, Math.sin(a) * d); crystal.rotation.z = (Math.random() - .5) * .5; scene.add(crystal);
      }
      const tex = auroraTexture(); tex.repeat.x = 2;
      const curtains = [[-38, 8, 110, 0], [-30, 10, 90, 1], [-50, 12, 130, 2]].map(([z, y, w, phase]) => {
        const sheet = new THREE.PlaneGeometry(w, 14, 120, 1);
        const m = new THREE.Mesh(sheet, new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: .55, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide, fog: false }));
        m.position.set(0, y, z); m.userData = { base: Float32Array.from(sheet.attributes.position.array), phase: phase * 2 }; scene.add(m); return m;
      });
      const stars = [];
      for (let i = 0; i < 900; i++) stars.push((Math.random() - .5) * 220, 6 + Math.random() * 60, -30 - Math.random() * 90);
      const starGeo = new THREE.BufferGeometry(); starGeo.setAttribute("position", new THREE.Float32BufferAttribute(stars, 3));
      scene.add(new THREE.Points(starGeo, new THREE.PointsMaterial({ color: 0xffffff, size: .25, fog: false })));
      const moonGlow = sprite(glowTexture(), 10, { color: 0xdfe8ff, opacity: .8, fog: false }); moonGlow.position.set(28, 22, -70); scene.add(moonGlow);
      const snow = risingPoints(1400, 44, -1.9, 14, 0xffffff, .07); snow.material.blending = THREE.NormalBlending; scene.add(snow);
      const sparkles = [0, 1].map(() => {   // frost glinting on the snow, two sets twinkling in turn
        const pts = [];
        for (let i = 0; i < 160; i++) { const a = Math.random() * Math.PI * 2, d = 4 + Math.random() * 12; pts.push(Math.cos(a) * d, -1.75 + Math.random() * .15, Math.sin(a) * d); }
        const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
        const p = new THREE.Points(geo, new THREE.PointsMaterial({ color: 0xe8f6ff, size: .09, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }));
        scene.add(p); return p;
      });
      scene.userData.aurora = { curtains, snow, sparkles };
    },
    tick(scene, t, dt, fx) {
      const sky = scene.userData.aurora;
      for (const c of sky.curtains) {
        const p = c.geometry.attributes.position, base = c.userData.base;
        for (let i = 0; i < p.count; i++) { const x = base[i * 3]; p.setZ(i, Math.sin(x * .06 + t * .5 + c.userData.phase) * 6 + Math.sin(x * .17 - t * .8) * 1.5); }
        p.needsUpdate = true;
        c.material.opacity = .45 + Math.sin(t * .7 + c.userData.phase) * .15;
      }
      moveParticles(sky.snow, t, dt, -.8);
      sky.sparkles.forEach((s, i) => { s.material.opacity = .25 + .75 * Math.max(0, Math.sin(t * 2.2 + i * Math.PI)); });
      every(scene, "star", 4, t, () => {
        const from = new THREE.Vector3((Math.random() - .5) * 120, 28 + Math.random() * 20, -60 - Math.random() * 20);
        fx.shootingStar(0xdff4ff, from, from.clone().add(new THREE.Vector3((Math.random() < .5 ? -1 : 1) * 26, -12, 0)), 1000);
      });
    },
    node(kind, color) {
      const g = new THREE.Group(), add = THREE.AdditiveBlending, snow = mat(0xf2f7ff, { roughness: 1 });
      const ice = new THREE.MeshStandardMaterial({ color: 0xd8f4ff, transparent: true, opacity: .6, roughness: .05, metalness: .1, emissive: color, emissiveIntensity: .4 });
      ice.userData.keepColor = true;
      if (kind === "user") {   // a log cabin with warm windows and a snowy roof
        const box = (w, h, d, c, x, y, z) => { const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat(c)); m.position.set(x, y, z); g.add(m); return m; };
        box(1.6, 1.1, 1.3, 0x6a4026, 0, -1.05, 0);
        const roof = new THREE.Mesh(new THREE.ConeGeometry(1.4, .95, 4), snow); roof.rotation.y = Math.PI / 4; roof.position.y = -.02; g.add(roof);
        box(.22, .5, .22, 0x5a5a5a, .45, .2, -.2);
        for (const x of [-.45, .45]) {
          const win = new THREE.Mesh(new THREE.PlaneGeometry(.3, .3), new THREE.MeshBasicMaterial({ color: 0xffc46a })); win.position.set(x, -.95, .66); g.add(win);
        }
        box(.35, .6, .04, 0x3a2414, 0, -1.3, .66);
        const warm = new THREE.PointLight(0xffb060, 4, 5); warm.position.set(0, -.8, 1.2); g.add(warm);
        const beacon = sprite(glowTexture(), .8, { color, blending: add }); beacon.position.y = .7; g.add(beacon);
        g.traverse(o => { if (o.isMesh) o.castShadow = true; });
        g.userData = { tint: [beacon.material], spin: [], float: null, height: 2 };
        return g;
      }
      if (kind === "jarvis") {   // a cluster of great ice crystals around a heart of light
        const cluster = new THREE.Group();
        for (const [x, z, h, w] of [[0, 0, 2.8, .6], [.75, .3, 1.8, .42], [-.65, .4, 2, .45], [.2, -.75, 1.6, .38], [-.45, -.55, 1.35, .32]]) {
          const shard = new THREE.Mesh(new THREE.OctahedronGeometry(1, 0), ice);
          shard.scale.set(w, h / 2, w); shard.position.set(x, -1.6 + h / 2, z); shard.rotation.set(x * .25, 0, -x * .3); cluster.add(shard);
        }
        const heart = sprite(glowTexture(), 3.4, { color, opacity: .85, blending: add }); heart.position.y = -.2;
        const mound = new THREE.Mesh(new THREE.SphereGeometry(1.6, 20, 10, 0, Math.PI * 2, 0, Math.PI / 2), snow); mound.scale.y = .3; mound.position.y = -1.7;
        g.add(mound, cluster, heart);
        g.traverse(o => { if (o.isMesh) o.castShadow = true; });
        g.userData = { tint: [ice, heart.material], spin: [[cluster, 0, .12, 0]], float: null, height: 2.6 };
        return g;
      }
      // a floating ice crystal with a glowing core and little shards circling it, above a snow mound
      const holder = new THREE.Group(), pivot = new THREE.Group();
      const crystal = new THREE.Mesh(new THREE.OctahedronGeometry(.55, 0), ice); crystal.scale.y = 1.8; crystal.castShadow = true;
      const core = sprite(glowTexture(), 1.6, { color, blending: add, opacity: .9 });
      for (let i = 0; i < 3; i++) {
        const bit = new THREE.Mesh(new THREE.OctahedronGeometry(.12, 0), ice), a = i / 3 * Math.PI * 2;
        bit.position.set(Math.cos(a) * 1.1, Math.sin(i * 2) * .3, Math.sin(a) * 1.1); pivot.add(bit);
      }
      holder.add(crystal, core, pivot); holder.position.y = .1;
      const mound = new THREE.Mesh(new THREE.SphereGeometry(.9, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2), snow); mound.scale.y = .35; mound.position.y = -1.75;
      const ring = new THREE.Mesh(new THREE.RingGeometry(.95, 1.1, 48), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .7, side: THREE.DoubleSide }));
      ring.rotation.x = -Math.PI / 2; ring.position.y = -1.55;
      g.add(mound, ring, holder);
      g.userData = { tint: [ice, core.material, ring.material], spin: [[crystal, 0, .5, 0], [pivot, 0, .9, 0]], float: holder, height: 1.9 };
      return g;
    },
    link: color => new THREE.LineBasicMaterial({ color, transparent: true, opacity: .45 }),
    particle(color) { return sprite(glowTexture(), .85, { color, blending: THREE.AdditiveBlending }); },
    burst(scene, at, fx) { fx.ring(at, 0xfff1a8, 3); fx.spawn(at, 0x5cffc8); },
  },

  zen: {   // a Japanese garden at dusk: raked gravel, a koi pond and bridge, a torii gate, stone lanterns, falling blossom
    background: 0xf2c4ae, bloom: [0.35, 0.5, 0.82], matte: true, linkY: -1.5, msg: 0x9a5aa8, home: [[0, 10, 14], [0, -1, 1]],
    status: { starting: 0xff9a3c, working: 0xff8a2a, waiting: 0x6f86c4, "needs you": 0xd2302a, done: 0x4f9a3c, stopped: 0x8a8580, failed: 0xd2302a },
    world(scene) {
      scene.background = skyTexture("zen", [[0, "#5d5a9c"], [.38, "#c99abb"], [.62, "#f6b9a2"], [.8, "#ffd9b0"], [1, "#f2c4ae"]]);
      scene.fog = new THREE.Fog(0xf2c4ae, 30, 95);
      scene.add(new THREE.HemisphereLight(0xffe2cc, 0x6a5a48, 1.05));
      const sun = new THREE.DirectionalLight(0xffc89a, 1.5); sun.position.set(-14, 12, -10); sun.castShadow = true;
      sun.shadow.mapSize.set(2048, 2048); Object.assign(sun.shadow.camera, { left: -24, right: 24, top: 24, bottom: -24 }); scene.add(sun);
      const fill = new THREE.DirectionalLight(0xffe8d8, .7); fill.position.set(6, 8, 14); scene.add(fill);   // soft light from the viewer's side
      // the raked gravel bed, edged with stones, in a mossy garden
      const gravel = new THREE.Mesh(new THREE.CircleGeometry(13.5, 96), new THREE.MeshStandardMaterial({ map: rakedSandTexture(), roughness: 1 }));
      gravel.rotation.x = -Math.PI / 2; gravel.position.y = -1.6; gravel.receiveShadow = true; scene.add(gravel);
      const moss = new THREE.Mesh(new THREE.CircleGeometry(140, 64), mat(0x6f7d46, { roughness: 1 }));
      moss.rotation.x = -Math.PI / 2; moss.position.y = -1.64; moss.receiveShadow = true; scene.add(moss);
      const edge = new THREE.InstancedMesh(new THREE.DodecahedronGeometry(.32, 0), mat(0x8e8a80), 120), m4 = new THREE.Matrix4();
      for (let i = 0; i < 120; i++) {
        const a = i / 120 * Math.PI * 2, k = .8 + Math.random() * .5;
        m4.compose(new THREE.Vector3(Math.cos(a) * 13.7, -1.58, Math.sin(a) * 13.7), new THREE.Quaternion().setFromEuler(new THREE.Euler(Math.random(), Math.random() * 3, 0)), new THREE.Vector3(k, k * .6, k));
        edge.setMatrixAt(i, m4);
      }
      edge.castShadow = edge.receiveShadow = true; scene.add(edge);
      for (const [x, z, s] of [[11.6, -1.5, 1], [-11.8, 2.6, .8], [10.4, 7.6, .6]]) {   // rocks with the gravel raked round them
        const rock = new THREE.Mesh(new THREE.DodecahedronGeometry(s, 1), mat(0x6f6a62)); rock.scale.y = .7; rock.position.set(x, -1.6 + s * .3, z);
        rock.rotation.y = x; rock.castShadow = true; scene.add(rock);
        for (const r of [s * 1.35, s * 1.75, s * 2.15]) {
          const ring = new THREE.Mesh(new THREE.RingGeometry(r - .05, r, 48), new THREE.MeshBasicMaterial({ color: 0xb8a684, transparent: true, opacity: .8 }));
          ring.rotation.x = -Math.PI / 2; ring.position.set(x, -1.59, z); scene.add(ring);
        }
      }
      // far hills in layers of mist, and the low evening sun
      const hills = new THREE.Mesh(new THREE.PlaneGeometry(260, 65), new THREE.MeshBasicMaterial({ map: ridgeTexture("zen",
        [["#b99bb8", .45, .3], ["#9a7fa3", .3, .25], ["#7c6a8c", .16, .18]]), transparent: true, fog: false, depthWrite: false }));
      hills.position.set(0, 14, -90); scene.add(hills);
      const sunDisk = sprite(glowTexture(), 26, { color: 0xffe0b0, opacity: .9, fog: false }); sunDisk.position.set(-30, 12, -95); scene.add(sunDisk);
      // a stone lantern for the scenery, lit warm
      const toro = (x, z, s) => {
        const t = new THREE.Group(), stone = mat(0x9b968b);
        const part = (geo, y) => { const m = new THREE.Mesh(geo, stone); m.position.y = y; m.castShadow = true; t.add(m); return m; };
        part(new THREE.CylinderGeometry(.42, .5, .22, 6), .11); part(new THREE.CylinderGeometry(.13, .16, .75, 8), .6);
        part(new THREE.CylinderGeometry(.42, .36, .14, 6), 1.03); part(new THREE.BoxGeometry(.48, .42, .48), 1.31);
        part(new THREE.ConeGeometry(.6, .38, 6), 1.71); part(new THREE.SphereGeometry(.1, 8, 6), 1.95);
        const light = new THREE.Mesh(new THREE.BoxGeometry(.5, .26, .3), new THREE.MeshBasicMaterial({ color: 0xffc76a })); light.position.y = 1.31; t.add(light);
        const halo = sprite(glowTexture(), 1.4, { color: 0xffb35a, opacity: .75, blending: THREE.AdditiveBlending }); halo.position.y = 1.31; t.add(halo);
        t.scale.setScalar(s); t.position.set(x, -1.62, z); scene.add(t); return t;
      };
      // the torii gate behind everything, with lanterns either side
      const torii = new THREE.Group(), red = mat(0xc8372d), ink = mat(0x2a2424);
      for (const x of [-2.7, 2.7]) {
        const post = new THREE.Mesh(new THREE.CylinderGeometry(.24, .3, 5.4, 12), red); post.position.set(x, 2.7, 0); torii.add(post);
        const foot = new THREE.Mesh(new THREE.CylinderGeometry(.36, .36, .4, 12), ink); foot.position.set(x, .2, 0); torii.add(foot);
      }
      const beam = (w, h, d, y, m) => { const b = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), m); b.position.y = y; torii.add(b); return b; };
      beam(6.6, .3, .32, 4.3, red); beam(7.4, .3, .5, 5.15, red); beam(8.2, .26, .62, 5.42, ink);
      const plaque = beam(.5, .7, .1, 4.75, ink); plaque.position.z = .2;
      for (const side of [-1, 1]) { const tip = new THREE.Mesh(new THREE.BoxGeometry(.7, .26, .62), ink); tip.position.set(side * 4.25, 5.55, 0); tip.rotation.z = side * .22; torii.add(tip); }
      torii.position.set(0, -1.62, -19); torii.traverse(o => { if (o.isMesh) o.castShadow = true; }); scene.add(torii);
      toro(-4.2, -17.5, 1.1); toro(4.2, -17.5, 1.1); toro(-13.6, -3.4, .9); toro(13.6, -6.4, .9); toro(-12.4, 9.6, .8); toro(12.6, 10.8, .8);
      // the koi pond with a curved red bridge
      const pond = new THREE.Group(); pond.position.set(-18, 0, -9); scene.add(pond);
      const water = new THREE.Mesh(new THREE.CircleGeometry(4.6, 48), new THREE.MeshStandardMaterial({ color: 0x3f6f7c, roughness: .15, metalness: .2, transparent: true, opacity: .82 }));
      water.rotation.x = -Math.PI / 2; water.scale.y = .72; water.position.y = -1.6; pond.add(water);
      const rim = new THREE.InstancedMesh(new THREE.DodecahedronGeometry(.45, 0), mat(0x7d786e), 46);
      for (let i = 0; i < 46; i++) {
        const a = i / 46 * Math.PI * 2, k = .7 + Math.random() * .6;
        m4.compose(new THREE.Vector3(Math.cos(a) * 4.8, -1.55, Math.sin(a) * 3.5), new THREE.Quaternion().setFromEuler(new THREE.Euler(Math.random(), Math.random(), 0)), new THREE.Vector3(k, k * .55, k));
        rim.setMatrixAt(i, m4);
      }
      pond.add(rim);
      for (let i = 0; i < 9; i++) {   // lily pads
        const pad = new THREE.Mesh(new THREE.CircleGeometry(.32 + Math.random() * .15, 16, .3, Math.PI * 2 - .3), mat(0x4f7d3a, { side: THREE.DoubleSide }));
        pad.rotation.x = -Math.PI / 2; pad.rotation.z = Math.random() * 6; pad.position.set((Math.random() - .5) * 6, -1.58, (Math.random() - .5) * 4.2); pond.add(pad);
        if (i % 3 === 0) { const bloom = new THREE.Mesh(new THREE.SphereGeometry(.12, 8, 6), mat(0xffc2d6)); bloom.position.copy(pad.position).setY(-1.5); pond.add(bloom); }
      }
      const koi = Array.from({ length: 6 }, (_, i) => {
        const fish = new THREE.Group(), body = new THREE.Mesh(new THREE.SphereGeometry(.24, 10, 8), mat(i % 3 === 1 ? 0xf6f1e8 : 0xff6a22));
        body.scale.set(1.7, .45, .62); fish.add(body);
        const tail = new THREE.Mesh(new THREE.ConeGeometry(.16, .32, 4), mat(i % 2 ? 0xffffff : 0xff8a3a)); tail.rotation.z = Math.PI / 2; tail.position.x = -.5; tail.scale.z = .3; fish.add(tail);
        if (i % 3 !== 1) { const spot = new THREE.Mesh(new THREE.SphereGeometry(.13, 8, 6), mat(0xffffff)); spot.position.set(.12, .06, 0); spot.scale.set(1.4, .5, .9); fish.add(spot); }
        fish.userData = { a: i * 1.05, r: 1.2 + (i % 3) * .9, speed: (.25 + (i % 4) * .08) * (i % 2 ? 1 : -1), tail }; pond.add(fish); return fish;
      });
      const bridge = new THREE.Group(), wood = mat(0xc8372d), plank = mat(0x6a4a34);
      const span = new THREE.QuadraticBezierCurve3(new THREE.Vector3(-5.4, -1.5, 0), new THREE.Vector3(0, .6, 0), new THREE.Vector3(5.4, -1.5, 0));
      for (let i = 0; i <= 20; i++) {
        const p = span.getPoint(i / 20), tan = span.getTangent(i / 20);
        const board = new THREE.Mesh(new THREE.BoxGeometry(.5, .08, 1.5), plank); board.position.copy(p); board.rotation.z = Math.atan2(tan.y, tan.x); bridge.add(board);
        if (i % 4 === 0) for (const z of [-.72, .72]) { const post = new THREE.Mesh(new THREE.CylinderGeometry(.05, .05, .7, 6), wood); post.position.set(p.x, p.y + .35, z); bridge.add(post); }
      }
      for (const z of [-.72, .72]) {
        const pts = span.getPoints(20).map(p => p.clone().setZ(z).setY(p.y + .68));
        bridge.add(new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 40, .055, 6), wood));
      }
      bridge.rotation.y = .5; bridge.traverse(o => { if (o.isMesh) o.castShadow = true; }); pond.add(bridge);
      // cherry trees in blossom, and a stand of bamboo
      const sakura = (x, z, s) => {
        const tree = new THREE.Group(), bark = mat(0x4a3328), rnd = seeded(Math.round(x * 31 + z * 17) + 999);
        const limb = (from, to, r) => {
          const len = from.distanceTo(to), m = new THREE.Mesh(new THREE.CylinderGeometry(r * .7, r, len, 6), bark);
          m.position.copy(from).add(to).multiplyScalar(.5); m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), to.clone().sub(from).normalize()); tree.add(m);
        };
        const fork = new THREE.Vector3(.3, 2.2, 0); limb(new THREE.Vector3(0, 0, 0), fork, .28);
        for (let i = 0; i < 4; i++) {
          const a = i / 4 * Math.PI * 2 + rnd(), tip = new THREE.Vector3(Math.cos(a) * 1.6, 3.3 + rnd() * .6, Math.sin(a) * 1.6).add(new THREE.Vector3(.3, 0, 0));
          limb(fork, tip, .14);
        }
        const pinks = [0xf7b6c8, 0xf4a3bb, 0xfbd0dc, 0xee9db6];
        for (let i = 0; i < 11; i++) {
          const a = rnd() * Math.PI * 2, d = rnd() * 1.7, puff = new THREE.Mesh(new THREE.IcosahedronGeometry(.8 + rnd() * .6, 0), mat(pinks[i % 4], { roughness: 1 }));
          puff.position.set(.3 + Math.cos(a) * d, 3.4 + rnd() * 1.3, Math.sin(a) * d); tree.add(puff);
        }
        tree.scale.setScalar(s); tree.position.set(x, -1.62, z); tree.traverse(o => { if (o.isMesh) o.castShadow = true; }); scene.add(tree);
      };
      for (const [x, z, s] of [[-8, -20, 1.3], [9, -21, 1.4], [-25, -15, 1.2], [21, -3, 1.1], [-22, 4, 1], [16, -24, 1], [-15, -25, 1.1], [24, 9, .9], [-20, 14, .9]]) sakura(x, z, s);
      const bamboo = new THREE.Group(), green = mat(0x7da35a), leaf = mat(0x5f8f3a, { side: THREE.DoubleSide });
      for (let i = 0; i < 26; i++) {
        const h = 5 + Math.random() * 4, stalk = new THREE.Group();
        const pole = new THREE.Mesh(new THREE.CylinderGeometry(.08, .1, h, 6), green); pole.position.y = h / 2; stalk.add(pole);
        for (let y = 1; y < h; y += 1.1) { const node = new THREE.Mesh(new THREE.TorusGeometry(.1, .025, 4, 8), mat(0x5d7d3e)); node.rotation.x = Math.PI / 2; node.position.y = y; stalk.add(node); }
        for (let j = 0; j < 4; j++) { const l = new THREE.Mesh(new THREE.PlaneGeometry(.7, .14), leaf); l.position.set(.3, h - j * .5, 0); l.rotation.set(0, j * 1.7, -.4); stalk.add(l); }
        stalk.position.set((Math.random() - .5) * 6, 0, (Math.random() - .5) * 5); stalk.rotation.set((Math.random() - .5) * .12, 0, (Math.random() - .5) * .12); bamboo.add(stalk);
      }
      bamboo.position.set(18, -1.62, -14); bamboo.traverse(o => { if (o.isMesh) o.castShadow = true; }); scene.add(bamboo);
      const petals = risingPoints(700, 46, -1.6, 13, 0xffb7cc, .22);
      Object.assign(petals.material, { map: petalTexture(), blending: THREE.NormalBlending, alphaTest: .3, opacity: 1 }); scene.add(petals);
      const flies = risingPoints(70, 34, -1.2, 2, 0xffe9a0, .14); flies.material.opacity = .9; scene.add(flies);
      scene.userData.zen = { koi, petals, flies, pond };
    },
    tick(scene, t, dt, fx) {
      const z = scene.userData.zen;
      for (const fish of z.koi) {
        const u = fish.userData, a = u.a + t * u.speed;
        fish.position.set(Math.cos(a) * u.r * 1.25, -1.68, Math.sin(a) * u.r * .8);
        fish.rotation.y = -a + (u.speed > 0 ? -Math.PI / 2 : Math.PI / 2); u.tail.rotation.y = Math.sin(t * 6 + u.a) * .5;
      }
      moveParticles(z.petals, t, dt, -.55);
      const p = z.petals.geometry.attributes.position;
      for (let i = 0; i < p.count; i += 3) p.setX(i, p.getX(i) + dt * .25);   // a light breeze
      moveParticles(z.flies, t, dt, .05);
      z.flies.material.opacity = .55 + Math.sin(t * 2.3) * .35;
      every(scene, "ripple", 3, t, () => fx.ping(z.pond.position.clone().add(new THREE.Vector3((Math.random() - .5) * 5, -1.57, (Math.random() - .5) * 3.4)), 0xffffff, 1.6, 2200));
      every(scene, "bell", 9, t, () => fx.ping(new THREE.Vector3(0, -1.57, 0), 0xc8372d, 13, 4200));   // a temple bell rolling out across the gravel
    },
    node(kind, color) {
      const g = new THREE.Group(), add = THREE.AdditiveBlending, stone = mat(0x9b968b);
      const glowPane = new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 1.1, roughness: .6 });
      if (kind === "jarvis") {   // a three-storey pagoda, its windows lit in Jarvis's colour
        const base = new THREE.Mesh(new THREE.BoxGeometry(3, .32, 3), stone); base.position.y = -1.44; g.add(base);
        const walls = mat(0xf3e8d2), roofMat = mat(0x3c3a46), trim = mat(0xc8372d);
        let y = -1.28;
        [2.2, 1.75, 1.3].forEach(w => {
          const body = new THREE.Mesh(new THREE.BoxGeometry(w, .62, w), walls); body.position.y = y + .31; g.add(body);
          for (let i = 0; i < 4; i++) {
            const pane = new THREE.Mesh(new THREE.PlaneGeometry(w * .42, .3), glowPane), a = i * Math.PI / 2;
            pane.position.set(Math.sin(a) * (w / 2 + .01), y + .32, Math.cos(a) * (w / 2 + .01)); pane.rotation.y = a; g.add(pane);
          }
          const rail = new THREE.Mesh(new THREE.BoxGeometry(w + .14, .08, w + .14), trim); rail.position.y = y + .64; g.add(rail);
          const roof = new THREE.Mesh(new THREE.ConeGeometry(w * 1.02, .5, 4), roofMat); roof.rotation.y = Math.PI / 4; roof.position.y = y + .9; g.add(roof);
          y += 1.04;
        });
        const spire = new THREE.Mesh(new THREE.CylinderGeometry(.04, .06, .9, 6), mat(0xd4a640, { metalness: .6, roughness: .3 })); spire.position.y = y + .2; g.add(spire);
        for (let i = 0; i < 4; i++) { const r = new THREE.Mesh(new THREE.TorusGeometry(.1, .025, 4, 12), spire.material); r.rotation.x = Math.PI / 2; r.position.y = y + i * .16; g.add(r); }
        const lamp = new THREE.PointLight(0xffb070, 8, 9); lamp.position.set(0, 0, 2); g.add(lamp);
        const aura = sprite(glowTexture(), 5, { color, opacity: .28, blending: add }); aura.position.y = -.2; g.add(aura);
        g.traverse(o => { if (o.isMesh) o.castShadow = true; });
        g.userData = { tint: [glowPane, aura.material], spin: [], float: null, height: 4.5, ground: true };
        return g;
      }
      if (kind === "user") {   // a tea house on a wooden deck, its paper walls glowing
        const wood = mat(0x6b4a32), roofMat = mat(0x3c3a46);
        const deck = new THREE.Mesh(new THREE.BoxGeometry(2.3, .22, 1.9), wood); deck.position.y = -1.42; g.add(deck);
        const shoji = new THREE.Mesh(new THREE.BoxGeometry(1.7, .95, 1.3), new THREE.MeshStandardMaterial({ color: 0xf6efdd, emissive: 0x8a5a20, emissiveIntensity: .5 }));
        shoji.position.y = -.83; g.add(shoji);
        for (const x of [-.85, -.42, 0, .42, .85]) { const bar = new THREE.Mesh(new THREE.BoxGeometry(.04, .95, 1.34), wood); bar.position.set(x, -.83, 0); g.add(bar); }
        const sill = new THREE.Mesh(new THREE.BoxGeometry(1.76, .05, 1.36), wood); sill.position.y = -.6; g.add(sill);
        const roof = new THREE.Mesh(new THREE.ConeGeometry(1.75, .75, 4), roofMat); roof.rotation.y = Math.PI / 4; roof.scale.z = .8; roof.position.y = .0; g.add(roof);
        const lantern = new THREE.Mesh(new THREE.SphereGeometry(.16, 12, 8), new THREE.MeshBasicMaterial({ color: 0xff5a3a })); lantern.scale.y = 1.3; lantern.position.set(.95, -.55, .82); g.add(lantern);
        const beacon = sprite(glowTexture(), 1, { color, blending: add }); beacon.position.y = .75; g.add(beacon);
        g.traverse(o => { if (o.isMesh) o.castShadow = true; });
        g.userData = { tint: [beacon.material], spin: [], float: null, height: 2.1, ground: true };
        return g;
      }
      // a stone lantern lit in the agent's status colour
      const part = (geo, y) => { const m = new THREE.Mesh(geo, stone); m.position.y = y; m.castShadow = true; g.add(m); return m; };
      part(new THREE.CylinderGeometry(.45, .55, .22, 6), -1.49); part(new THREE.CylinderGeometry(.14, .18, .75, 8), -1.0);
      part(new THREE.CylinderGeometry(.45, .38, .14, 6), -.56); part(new THREE.BoxGeometry(.52, .46, .52), -.26);
      for (let i = 0; i < 4; i++) {
        const pane = new THREE.Mesh(new THREE.PlaneGeometry(.3, .28), glowPane), a = i * Math.PI / 2;
        pane.position.set(Math.sin(a) * .265, -.26, Math.cos(a) * .265); pane.rotation.y = a; g.add(pane);
      }
      part(new THREE.ConeGeometry(.66, .4, 6), .17); part(new THREE.SphereGeometry(.11, 8, 6), .43);
      const halo = sprite(glowTexture(), 1.9, { color, opacity: .8, blending: add }); halo.position.y = -.26; g.add(halo);
      const ring = new THREE.Mesh(new THREE.RingGeometry(.8, .92, 40), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .75, side: THREE.DoubleSide }));
      ring.rotation.x = -Math.PI / 2; ring.position.y = -1.58; g.add(ring);
      g.userData = { tint: [glowPane, halo.material, ring.material], spin: [], float: null, height: 1.75, ground: true };
      return g;
    },
    link: color => new THREE.LineDashedMaterial({ color, dashSize: .35, gapSize: .25, transparent: true, opacity: .8 }),
    particle(color, kind) { return sprite(emojiTexture(kind === "result" ? "🌸" : kind === "user" ? "🎐" : "🏮"), 1); },
    burst(scene, at, fx) { fx.ring(at, 0xc8372d, 3); fx.ping(at.clone().setY(-1.57), 0xffb7cc, 4, 1600); },
  },

  clockwork: {   // a steampunk workshop: great brass gears turning in the dark, a clock that keeps real time, pipes, a tesla coil
    background: 0x140d07, bloom: [0.7, 0.45, 0.35], linkY: -1.45, msg: 0xffcf7a, home: [[0, 9, 14], [0, -.4, 1]],
    status: { starting: 0xffc46a, working: 0xffb347, waiting: 0x6fc2c9, "needs you": 0xff4a3a, done: 0xa8e06a, stopped: 0x5a4a3e, failed: 0xff4a3a },
    world(scene) {
      scene.fog = new THREE.Fog(0x140d07, 26, 70);
      scene.add(new THREE.HemisphereLight(0xffd9a0, 0x2a1808, .55));
      const lamp = new THREE.SpotLight(0xffd8a0, 900, 60, .75, .6, 2); lamp.position.set(-3, 18, 8); lamp.castShadow = true;
      lamp.shadow.mapSize.set(2048, 2048); scene.add(lamp, lamp.target);
      for (const x of [-15, 14]) { const wash = new THREE.PointLight(0xffb060, 120, 26, 1.6); wash.position.set(x, 6, -18); scene.add(wash); }   // light on the gear walls
      const brass = mat(0xc4953f, { metalness: .5, roughness: .38, flatShading: false }), copper = mat(0xb8673a, { metalness: .5, roughness: .4, flatShading: false });
      const iron = mat(0x2e2620, { metalness: .4, roughness: .6 });
      // the floor: old boards, ringed with brass and rivets
      const floor = new THREE.Mesh(new THREE.CircleGeometry(14, 96), new THREE.MeshStandardMaterial({ map: plankTexture(), roughness: .85 }));
      floor.rotation.x = -Math.PI / 2; floor.position.y = -1.6; floor.receiveShadow = true; scene.add(floor);
      const outer = new THREE.Mesh(new THREE.CircleGeometry(120, 48), mat(0x1c120a)); outer.rotation.x = -Math.PI / 2; outer.position.y = -1.62; scene.add(outer);
      const band = new THREE.Mesh(new THREE.TorusGeometry(14, .16, 8, 140), brass); band.rotation.x = Math.PI / 2; band.position.y = -1.56; scene.add(band);
      for (const r of [4.5, 9.5]) {
        const ring = new THREE.Mesh(new THREE.RingGeometry(r - .04, r + .04, 120), new THREE.MeshBasicMaterial({ color: 0xc4953f, transparent: true, opacity: .5 }));
        ring.rotation.x = -Math.PI / 2; ring.position.y = -1.59; scene.add(ring);
      }
      const rivets = new THREE.InstancedMesh(new THREE.SphereGeometry(.07, 8, 6), brass, 96), m4 = new THREE.Matrix4();
      for (let i = 0; i < 96; i++) { const a = i / 96 * Math.PI * 2; rivets.setMatrixAt(i, m4.makeTranslation(Math.cos(a) * 14.35, -1.57, Math.sin(a) * 14.35)); }
      scene.add(rivets);
      // a great clock face on the back wall, keeping the real time
      const c = document.createElement("canvas"); c.width = c.height = 512; const gc = c.getContext("2d");
      const face = gc.createRadialGradient(256, 256, 20, 256, 256, 256); face.addColorStop(0, "#f2e2bc"); face.addColorStop(.85, "#c9a868"); face.addColorStop(1, "#6a4a1c");
      gc.fillStyle = face; gc.beginPath(); gc.arc(256, 256, 252, 0, 7); gc.fill();
      gc.strokeStyle = "#3a2410"; gc.lineWidth = 3; gc.beginPath(); gc.arc(256, 256, 200, 0, 7); gc.stroke();
      for (let i = 0; i < 60; i++) {
        const a = i / 60 * Math.PI * 2, r1 = i % 5 ? 228 : 214;
        gc.lineWidth = i % 5 ? 2 : 5; gc.beginPath(); gc.moveTo(256 + Math.sin(a) * r1, 256 - Math.cos(a) * r1); gc.lineTo(256 + Math.sin(a) * 240, 256 - Math.cos(a) * 240); gc.stroke();
      }
      gc.fillStyle = "#2a1808"; gc.font = "bold 40px 'IM Fell English SC', Georgia, serif"; gc.textAlign = "center"; gc.textBaseline = "middle";
      ["XII", "I", "II", "III", "IIII", "V", "VI", "VII", "VIII", "IX", "X", "XI"].forEach((n, i) => {
        const a = i / 12 * Math.PI * 2; gc.fillText(n, 256 + Math.sin(a) * 170, 258 - Math.cos(a) * 170);
      });
      const faceTex = new THREE.CanvasTexture(c); faceTex.colorSpace = THREE.SRGBColorSpace;
      const clock = new THREE.Group(); clock.position.set(0, 9.5, -34); scene.add(clock);
      clock.add(new THREE.Mesh(new THREE.CircleGeometry(7, 64), new THREE.MeshStandardMaterial({ map: faceTex, roughness: .7, emissive: 0x3a2408, emissiveIntensity: .6 })));
      const bezel = new THREE.Mesh(new THREE.TorusGeometry(7.1, .4, 10, 96), brass); clock.add(bezel);
      const hand = (len, w, z) => {
        const pivot = new THREE.Group(), m = new THREE.Mesh(new THREE.BoxGeometry(w, len, .1), mat(0x1e140c, { metalness: .4 }));
        m.position.y = len / 2 - .4; pivot.position.z = z; pivot.add(m); clock.add(pivot); return pivot;
      };
      const hours = hand(3.6, .34, .12), minutes = hand(5.4, .22, .2), seconds = hand(5.8, .07, .28);
      seconds.children[0].material = new THREE.MeshBasicMaterial({ color: 0xb8402a });
      const hub = new THREE.Mesh(new THREE.CylinderGeometry(.35, .35, .5, 16), brass); hub.rotation.x = Math.PI / 2; hub.position.z = .3; clock.add(hub);
      // walls of interlocking gears: each turns the next, so neighbours go opposite ways at the right speed
      const gears = [];
      const train = (x, y, z, rotY, chain) => {
        const wall = new THREE.Group(); wall.position.set(x, y, z); wall.rotation.y = rotY; scene.add(wall);
        let at = new THREE.Vector2(0, 0), speed = .12, prev = null;
        chain.forEach(([r, teeth, angle], i) => {
          if (prev) {
            const d = (prev.r + r) * .9; at = at.clone().add(new THREE.Vector2(Math.cos(angle), Math.sin(angle)).multiplyScalar(d));
            speed = -prev.speed * prev.teeth / teeth;
          }
          const gear = new THREE.Mesh(gearGeometry(r, teeth, .5), i % 2 ? copper : brass);
          gear.position.set(at.x, at.y, i % 2 ? .35 : 0); gear.rotation.z = i % 2 ? Math.PI / teeth : 0; gear.castShadow = true; wall.add(gear);
          const axle = new THREE.Mesh(new THREE.CylinderGeometry(r * .12, r * .12, 1, 12), iron); axle.rotation.x = Math.PI / 2; axle.position.copy(gear.position); wall.add(axle);
          gears.push({ gear, speed }); prev = { r, teeth, speed };
        });
      };
      train(-15, 4, -27, .35, [[5, 24, 0], [3, 14, -.5], [2, 10, .6], [3.6, 18, 1.4], [1.6, 8, -.2]]);
      train(14, 2, -26, -.35, [[4.4, 20, 0], [2.6, 12, 2.6], [3.4, 16, 1.2], [1.8, 9, -.9], [2.8, 14, .2]]);
      train(-26, 1, -6, Math.PI / 2, [[3.6, 18, 0], [2.2, 10, 1], [3, 14, -.4]]);
      train(26, 2, -4, -Math.PI / 2, [[3, 14, 0], [2.4, 12, 2], [3.8, 18, 1.1]]);
      // copper pipes climbing out of the floor, with valve wheels and steam vents
      const vents = [];
      for (const pts of [[[-17, -1.6, -11], [-17, 3, -11], [-14, 6, -16], [-6, 6.4, -19]], [[16, -1.6, -13], [16, 4.5, -13], [11, 7.4, -18], [5, 7.6, -20]],
                         [[-19, -1.6, 6], [-19, 2.4, 6], [-20, 5, -2]], [[19, -1.6, 7], [19, 3, 7], [21, 5.5, 0]]]) {
        const curve = new THREE.CatmullRomCurve3(pts.map(p => new THREE.Vector3(...p)), false, "catmullrom", .1);
        const pipe = new THREE.Mesh(new THREE.TubeGeometry(curve, 64, .3, 10), copper); pipe.castShadow = true; scene.add(pipe);
        for (const k of [.12, .55]) {
          const p = curve.getPoint(k), collar = new THREE.Mesh(new THREE.TorusGeometry(.34, .09, 6, 16), brass); collar.position.copy(p);
          collar.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), curve.getTangent(k)); scene.add(collar);
        }
        const v = curve.getPoint(.3), wheel = new THREE.Group(); wheel.position.copy(v).add(new THREE.Vector3(0, 0, .55));
        wheel.add(new THREE.Mesh(new THREE.TorusGeometry(.45, .06, 6, 20), mat(0xb8402a, { metalness: .3 })));
        for (let s = 0; s < 3; s++) { const spoke = new THREE.Mesh(new THREE.BoxGeometry(.9, .06, .06), wheel.children[0].material); spoke.rotation.z = s * Math.PI / 3; wheel.add(spoke); }
        scene.add(wheel); vents.push(curve.getPoint(1));
      }
      // gas lamps glowing warm round the edge of the floor
      for (const a of [-2.2, -.95, 3.5, 5.9]) {
        const post = new THREE.Group(); post.position.set(Math.cos(a) * 16, -1.6, Math.sin(a) * 16);
        const pole = new THREE.Mesh(new THREE.CylinderGeometry(.08, .12, 4.2, 8), iron); pole.position.y = 2.1; post.add(pole);
        const cage = new THREE.Mesh(new THREE.CylinderGeometry(.32, .22, .6, 6, 1, true), new THREE.MeshBasicMaterial({ color: 0xffc46a, transparent: true, opacity: .55, side: THREE.DoubleSide }));
        cage.position.y = 4.4; post.add(cage);
        const cap = new THREE.Mesh(new THREE.ConeGeometry(.42, .35, 6), iron); cap.position.y = 4.85; post.add(cap);
        const flame = sprite(glowTexture(), 2.4, { color: 0xffb35a, blending: THREE.AdditiveBlending }); flame.position.y = 4.4; post.add(flame);
        const light = new THREE.PointLight(0xffa04a, 22, 18, 1.6); light.position.y = 4.3; post.add(light);
        scene.add(post);
      }
      // the tesla coil
      const coil = new THREE.Group(); coil.position.set(12.5, -1.6, -10.5);
      const plinth = new THREE.Mesh(new THREE.CylinderGeometry(.9, 1, .6, 8), mat(0x3a2414)); plinth.position.y = .3; coil.add(plinth);
      const tube = new THREE.Mesh(new THREE.CylinderGeometry(.32, .32, 3.2, 16), mat(0x2a1a10)); tube.position.y = 2.2; coil.add(tube);
      for (let i = 0; i < 26; i++) { const w = new THREE.Mesh(new THREE.TorusGeometry(.34, .03, 4, 20), copper); w.rotation.x = Math.PI / 2; w.position.y = .75 + i * .11; coil.add(w); }
      const top = new THREE.Mesh(new THREE.TorusGeometry(.7, .26, 12, 32), mat(0xd8d8e0, { metalness: .8, roughness: .2, flatShading: false })); top.rotation.x = Math.PI / 2; top.position.y = 4; coil.add(top);
      const spark = new THREE.PointLight(0xa58cff, 0, 14); spark.position.y = 4.2; coil.add(spark);
      coil.traverse(o => { if (o.isMesh) o.castShadow = true; }); scene.add(coil);
      const dust = risingPoints(260, 30, -1.4, 9, 0xffc98a, .05); dust.material.opacity = .45; scene.add(dust);
      scene.userData.clockwork = { gears, hours, minutes, seconds, vents, coil, spark, dust, zaps: [] };
    },
    tick(scene, t, dt, fx) {
      const w = scene.userData.clockwork;
      for (const { gear, speed } of w.gears) gear.rotation.z += speed * dt;
      const now = new Date(), s = now.getSeconds() + now.getMilliseconds() / 1000, m = now.getMinutes() + s / 60, h = (now.getHours() % 12) + m / 60;
      w.seconds.rotation.z = -Math.floor(s) / 60 * Math.PI * 2; w.minutes.rotation.z = -m / 60 * Math.PI * 2; w.hours.rotation.z = -h / 12 * Math.PI * 2;
      moveParticles(w.dust, t, dt, .12);
      every(scene, "steam", .5, t, () => fx.smoke(w.vents[Math.floor(Math.random() * w.vents.length)].clone()));
      every(scene, "tick", 6, t, () => fx.ping(new THREE.Vector3(0, -1.55, 0), 0xffc46a, 13));
      every(scene, "zap", 2.2, t, () => {   // lightning from the coil's crown: a jagged main bolt and a branch or two
        const from = w.coil.position.clone().add(new THREE.Vector3(0, 4.1, 0)), until = performance.now() + 220;
        for (let b = 0; b < 2 + Math.floor(Math.random() * 2); b++) {
          const a = Math.random() * Math.PI * 2, len = 1.6 + Math.random() * 2.2, to = from.clone().add(new THREE.Vector3(Math.cos(a) * len, (Math.random() - .7) * 2.4, Math.sin(a) * len));
          const pts = [from];
          for (let i = 1; i < 9; i++) pts.push(from.clone().lerp(to, i / 9).add(new THREE.Vector3((Math.random() - .5) * .45, (Math.random() - .5) * .45, (Math.random() - .5) * .45)));
          pts.push(to);
          const bolt = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: b ? 0xa58cff : 0xe6e0ff, transparent: true, blending: THREE.AdditiveBlending }));
          scene.add(bolt); w.zaps.push({ bolt, until });
        }
        w.spark.intensity = 40;
      });
      w.spark.intensity *= Math.max(0, 1 - dt * 10);
      w.zaps = w.zaps.filter(z => {
        const left = z.until - performance.now();
        if (left <= 0) { scene.remove(z.bolt); z.bolt.geometry.dispose(); z.bolt.material.dispose(); return false; }
        z.bolt.material.opacity = Math.random() < .3 ? .2 : 1; return true;
      });
    },
    node(kind, color) {
      const g = new THREE.Group(), add = THREE.AdditiveBlending;
      const brass = mat(0xc4953f, { metalness: .5, roughness: .38, flatShading: false }), copper = mat(0xb8673a, { metalness: .5, roughness: .4, flatShading: false });
      const iron = mat(0x2e2620, { metalness: .4, roughness: .6 }), wood = mat(0x4a2c16);
      const glow = new THREE.MeshBasicMaterial({ color });
      const at = (m, x, y, z) => { m.position.set(x, y, z); m.castShadow = true; g.add(m); return m; };
      if (kind === "jarvis") {   // a great brass orrery: a glowing sun with planets swinging round it on arms
        at(new THREE.Mesh(new THREE.CylinderGeometry(1.5, 1.7, .32, 24), wood), 0, -1.44, 0);
        at(new THREE.Mesh(new THREE.CylinderGeometry(1.15, 1.3, .26, 24), brass), 0, -1.15, 0);
        at(new THREE.Mesh(new THREE.CylinderGeometry(.09, .14, 1.6, 10), brass), 0, -.25, 0);
        const sun = at(new THREE.Mesh(new THREE.SphereGeometry(.5, 24, 16), glow), 0, .7, 0);
        const corona = sprite(glowTexture(), 3, { color, opacity: .7, blending: add }); corona.position.y = .7; g.add(corona);
        const spin = [];
        [[1.15, .14, 0x6fc2c9, .7], [1.7, .2, 0xb8673a, .42], [2.3, .17, 0xd8c08a, .26]].forEach(([r, size, c, speed], i) => {
          const orbit = new THREE.Mesh(new THREE.TorusGeometry(r, .02, 4, 96), brass); orbit.rotation.x = Math.PI / 2; orbit.position.y = .7; g.add(orbit);
          const arm = new THREE.Group(); arm.position.y = .7; arm.rotation.y = i * 2.1; g.add(arm);
          const rod = new THREE.Mesh(new THREE.BoxGeometry(r, .04, .04), brass); rod.position.x = r / 2; arm.add(rod);
          const planet = new THREE.Mesh(new THREE.SphereGeometry(size, 16, 12), mat(c, { metalness: .3, roughness: .5, flatShading: false })); planet.position.x = r; arm.add(planet);
          if (i === 1) { const ring = new THREE.Mesh(new THREE.TorusGeometry(size * 1.7, .02, 4, 32), brass); ring.rotation.x = 1.2; ring.position.x = r; arm.add(ring); }
          spin.push([arm, 0, speed, 0]);
        });
        const armillary = new THREE.Mesh(new THREE.TorusGeometry(.85, .035, 6, 64), brass); armillary.position.y = .7; armillary.rotation.x = .5; g.add(armillary);
        spin.push([armillary, 0, .3, 0]);
        g.userData = { tint: [glow, corona.material], spin, float: null, height: 2.7, ground: true };
        return g;
      }
      if (kind === "user") {   // a workbench, with a little airship hovering over it
        at(new THREE.Mesh(new THREE.BoxGeometry(2, .14, 1), wood), 0, -.72, 0);
        for (const [x, z] of [[-.88, -.4], [.88, -.4], [-.88, .4], [.88, .4]]) at(new THREE.Mesh(new THREE.BoxGeometry(.1, .82, .1), wood), x, -1.2, z);
        at(new THREE.Mesh(new THREE.BoxGeometry(.3, .22, .2), iron), -.6, -.54, .1);
        const gear = at(new THREE.Mesh(gearGeometry(.22, 9, .06), brass), .5, -.62, .15); gear.rotation.x = -Math.PI / 2;
        const ship = new THREE.Group(); ship.position.set(0, .55, 0); g.add(ship);
        const balloon = new THREE.Mesh(new THREE.SphereGeometry(.5, 20, 14), mat(0xa8482a, { flatShading: false })); balloon.scale.set(2, .8, .8); ship.add(balloon);
        for (const x of [-.6, 0, .6]) { const strap = new THREE.Mesh(new THREE.TorusGeometry(.42 * Math.cos(x / 1.1), .025, 4, 24), brass); strap.rotation.y = Math.PI / 2; strap.position.x = x; ship.add(strap); }
        const gondola = new THREE.Mesh(new THREE.BoxGeometry(.6, .16, .24), wood); gondola.position.y = -.55; ship.add(gondola);
        const fin = new THREE.Mesh(new THREE.ConeGeometry(.22, .4, 3), brass); fin.rotation.z = Math.PI / 2; fin.position.x = -1.05; fin.scale.z = .2; ship.add(fin);
        const prop = new THREE.Mesh(new THREE.BoxGeometry(.04, .42, .06), brass); prop.position.set(-.42, -.55, 0); ship.add(prop);
        const lamp = sprite(glowTexture(), .9, { color, blending: add }); lamp.position.set(.32, -.55, 0); ship.add(lamp);
        g.traverse(o => { if (o.isMesh) o.castShadow = true; });
        g.userData = { tint: [lamp.material], spin: [[prop, 9, 0, 0]], float: ship, height: 2.1, ground: true };
        return g;
      }
      // an automaton tower: a copper body with turning gears and a vacuum tube glowing in its status colour
      at(new THREE.Mesh(new THREE.CylinderGeometry(.55, .62, .24, 8), iron), 0, -1.48, 0);
      at(new THREE.Mesh(new THREE.CylinderGeometry(.36, .42, 1.15, 12), copper), 0, -.8, 0);
      for (const y of [-1.25, -.35]) { const b = at(new THREE.Mesh(new THREE.TorusGeometry(.4, .05, 6, 24), brass), 0, y, 0); b.rotation.x = Math.PI / 2; }
      const big = at(new THREE.Mesh(gearGeometry(.42, 12, .1), brass), 0, -.8, .44), small = at(new THREE.Mesh(gearGeometry(.22, 7, .1), brass), .44, -.5, .42);
      at(new THREE.Mesh(new THREE.CylinderGeometry(.3, .36, .1, 12), brass), 0, -.18, 0);
      const glass = at(new THREE.Mesh(new THREE.CylinderGeometry(.2, .2, .55, 16), new THREE.MeshStandardMaterial({ color: 0xffffff, transparent: true, opacity: .3, roughness: .05 })), 0, .17, 0);
      glass.material.userData.keepColor = true;
      at(new THREE.Mesh(new THREE.SphereGeometry(.2, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2), glass.material), 0, .44, 0);
      const filament = at(new THREE.Mesh(new THREE.CylinderGeometry(.04, .04, .4, 6), glow), 0, .15, 0);
      const bulb = sprite(glowTexture(), 1.3, { color, blending: add }); bulb.position.y = .18; g.add(bulb);
      const ring = new THREE.Mesh(new THREE.RingGeometry(.78, .88, 40), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .7, side: THREE.DoubleSide }));
      ring.rotation.x = -Math.PI / 2; ring.position.y = -1.58; g.add(ring);
      g.userData = { tint: [glow, bulb.material, ring.material], spin: [[big, 0, 0, 1.1], [small, 0, 0, -1.9]], float: null, height: 1.85, ground: true };
      return g;
    },
    link: color => new THREE.LineDashedMaterial({ color, dashSize: .18, gapSize: .12, transparent: true, opacity: .75 }),
    particle(color) { return sprite(glowTexture(), .9, { color, blending: THREE.AdditiveBlending }); },
    burst(scene, at, fx) { fx.fountain(at, [0xffd27a, 0xfff1c0], 22, 4); fx.ring(at, 0xc4953f, 3); },
  },

  mars: {   // a colony on Mars: red dust and craters, mesas on the horizon, habitat domes, solar farms, a rover, two moons
    background: 0xd9a27a, bloom: [0.45, 0.4, 0.8], linkY: -1.5, msg: 0x9fe8ff, home: [[0, 9.5, 14], [0, -.8, 1]],
    status: { starting: 0x3ee0ff, working: 0x3ee0ff, waiting: 0xc6b8ff, "needs you": 0xff3355, done: 0x6dff8a, stopped: 0x6a5a52, failed: 0xff3355 },
    world(scene) {
      scene.background = skyTexture("mars", [[0, "#7a5a52"], [.35, "#c7906a"], [.7, "#e6b48a"], [1, "#d9a27a"]]);
      scene.fog = new THREE.Fog(0xd9a27a, 30, 115);
      scene.add(new THREE.HemisphereLight(0xffe2c8, 0x5a2414, .95));
      const sun = new THREE.DirectionalLight(0xfff0e0, 1.9); sun.position.set(14, 16, -6); sun.castShadow = true;
      sun.shadow.mapSize.set(2048, 2048); Object.assign(sun.shadow.camera, { left: -26, right: 26, top: 26, bottom: -26 }); scene.add(sun);
      // the ground: flat where the colony stands, rising into dunes and ridges further out
      const geo = new THREE.PlaneGeometry(220, 220, 110, 110); geo.rotateX(-Math.PI / 2);
      const p = geo.attributes.position, colors = [], c = new THREE.Color(), rnd = seeded(41);
      for (let i = 0; i < p.count; i++) {
        const x = p.getX(i), z = p.getZ(i), far = Math.max(0, Math.hypot(x, z) - 16);
        p.setY(i, Math.sin(x * .21) * Math.cos(z * .17) * .12 + far * .09 * (Math.sin(x * .13 + z * .07) * .6 + .7));
        c.setHSL(.04 + rnd() * .015, .55, .36 + rnd() * .07 + Math.min(.08, far * .003)); colors.push(c.r, c.g, c.b);
      }
      geo.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3)); geo.computeVertexNormals();
      const ground = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 1, flatShading: true }));
      ground.position.y = -1.62; ground.receiveShadow = true; scene.add(ground);
      for (const [x, z, r] of [[11.6, -2.5, 1.5], [-12, 3.5, 1.2], [6, 12.5, 1], [-6, -14, 2.2], [-17, 11, 1.6]]) {   // craters
        const lip = new THREE.Mesh(new THREE.TorusGeometry(r, r * .2, 6, 28), mat(0xc4683a, { roughness: 1 })); lip.rotation.x = Math.PI / 2; lip.scale.z = .35;
        lip.position.set(x, -1.6, z); lip.receiveShadow = true; scene.add(lip);
        const pit = new THREE.Mesh(new THREE.CircleGeometry(r * .95, 24), mat(0x7e3418, { roughness: 1 })); pit.rotation.x = -Math.PI / 2; pit.position.set(x, -1.6, z); scene.add(pit);
      }
      const rocks = new THREE.InstancedMesh(new THREE.DodecahedronGeometry(.3, 0), mat(0x6a2c16, { roughness: 1 }), 180), m4 = new THREE.Matrix4();
      for (let i = 0; i < 180; i++) {
        const a = rnd() * Math.PI * 2, d = 11 + rnd() * 30, k = .3 + rnd() * 1.4;
        m4.compose(new THREE.Vector3(Math.cos(a) * d, -1.55 + Math.max(0, d - 16) * .05, Math.sin(a) * d), new THREE.Quaternion().setFromEuler(new THREE.Euler(rnd() * 3, rnd() * 3, 0)), new THREE.Vector3(k, k * .7, k));
        rocks.setMatrixAt(i, m4);
      }
      rocks.castShadow = rocks.receiveShadow = true; scene.add(rocks);
      for (let i = 0; i < 9; i++) {   // flat-topped mesas on the horizon, banded
        const a = -Math.PI * .1 - i * .37, d = 62 + (i % 3) * 14, h = 7 + (i % 4) * 3, w = 7 + (i % 3) * 4, mesa = new THREE.Group();
        for (let b = 0; b < 3; b++) {
          const layer = new THREE.Mesh(new THREE.CylinderGeometry(w * (1 - b * .12) * .82, w * (1 - b * .12), h / 3, 7), mat([0x9a4426, 0xa85230, 0x8e3c22][b]));
          layer.position.y = h / 6 + b * h / 3; mesa.add(layer);
        }
        mesa.position.set(Math.cos(a) * d, -1, Math.sin(a) * d); mesa.rotation.y = i; mesa.scale.z = .7; scene.add(mesa);
      }
      // the colony: domes joined by tunnels, a solar farm, a landing pad
      const white = mat(0xebe6dc), grey = mat(0x8a8580, { metalness: .3 }), lit = new THREE.MeshBasicMaterial({ color: 0xffd27a });
      const dome = (x, z, r) => {
        const d = new THREE.Group(); d.position.set(x, -1.6, z);
        const shell = new THREE.Mesh(new THREE.SphereGeometry(r, 14, 7, 0, Math.PI * 2, 0, Math.PI / 2), white); d.add(shell);
        const frame = new THREE.LineSegments(new THREE.EdgesGeometry(shell.geometry, 20), new THREE.LineBasicMaterial({ color: 0x8a8580 })); d.add(frame);
        const base = new THREE.Mesh(new THREE.CylinderGeometry(r * 1.03, r * 1.06, .4, 28), grey); base.position.y = .2; d.add(base);
        for (let i = 0; i < 10; i++) { const a = i / 10 * Math.PI * 2, win = new THREE.Mesh(new THREE.BoxGeometry(.32, .14, .05), lit); win.position.set(Math.cos(a) * r * 1.07, .26, Math.sin(a) * r * 1.07); win.rotation.y = -a + Math.PI / 2; d.add(win); }
        d.traverse(o => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } }); scene.add(d); return d;
      };
      const domes = [[-12, -16, 3.2], [-4.5, -19, 2.4], [5, -18.5, 2.8], [14, -15, 2]].map(([x, z, r]) => dome(x, z, r));
      for (let i = 0; i < domes.length - 1; i++) {
        const a = domes[i].position, b = domes[i + 1].position, len = a.distanceTo(b);
        const tunnel = new THREE.Mesh(new THREE.CylinderGeometry(.45, .45, len, 12), white); tunnel.position.copy(a).add(b).multiplyScalar(.5).setY(-1.2);
        tunnel.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), b.clone().sub(a).normalize()); tunnel.castShadow = true; scene.add(tunnel);
      }
      const panelMat = new THREE.MeshStandardMaterial({ map: solarTexture(), metalness: .4, roughness: .35 });
      for (let row = 0; row < 3; row++) for (let i = 0; i < 6; i++) {
        const panel = new THREE.Group(); panel.position.set(-24 + i * 2.1, -1.6, 2 + row * 2.6);
        const post = new THREE.Mesh(new THREE.CylinderGeometry(.05, .05, .9, 6), grey); post.position.y = .45; panel.add(post);
        const board = new THREE.Mesh(new THREE.BoxGeometry(1.9, .05, 1.2), [grey, grey, panelMat, grey, grey, grey]); board.position.y = .95; board.rotation.x = -.5; panel.add(board);
        panel.traverse(o => { if (o.isMesh) o.castShadow = true; }); scene.add(panel);
      }
      const pad = new THREE.Mesh(new THREE.CircleGeometry(3, 32), mat(0x5a4a44)); pad.rotation.x = -Math.PI / 2; pad.position.set(19, -1.58, 6); scene.add(pad);
      const padRing = new THREE.Mesh(new THREE.RingGeometry(2.3, 2.5, 32), new THREE.MeshBasicMaterial({ color: 0xffd27a })); padRing.rotation.x = -Math.PI / 2; padRing.position.set(19, -1.57, 6); scene.add(padRing);
      // a rover doing its rounds
      const rover = new THREE.Group(), body = new THREE.Mesh(new THREE.BoxGeometry(1.2, .32, .7), white); body.position.y = .55; rover.add(body);
      const wheels = [];
      for (const x of [-.45, 0, .45]) for (const z of [-.42, .42]) {
        const w = new THREE.Mesh(new THREE.CylinderGeometry(.2, .2, .14, 12), mat(0x3a3430)); w.rotation.x = Math.PI / 2; w.position.set(x, .2, z); rover.add(w); wheels.push(w);
      }
      const mast = new THREE.Mesh(new THREE.CylinderGeometry(.03, .03, .6, 6), grey); mast.position.set(.4, 1, 0); rover.add(mast);
      const head = new THREE.Mesh(new THREE.BoxGeometry(.18, .12, .22), grey); head.position.set(.42, 1.32, 0); rover.add(head);
      rover.traverse(o => { if (o.isMesh) o.castShadow = true; }); rover.position.y = -1.6; scene.add(rover);
      // a dust devil wandering far out
      const swirl = [];
      for (let i = 0; i < 500; i++) { const h = Math.random() * 11, a = Math.random() * Math.PI * 2, r = .3 + h * .22 + Math.random() * .3; swirl.push(Math.cos(a) * r, h, Math.sin(a) * r); }
      const devilGeo = new THREE.BufferGeometry(); devilGeo.setAttribute("position", new THREE.Float32BufferAttribute(swirl, 3));
      const devil = new THREE.Points(devilGeo, new THREE.PointsMaterial({ color: 0xc98a5e, size: .35, transparent: true, opacity: .45, depthWrite: false }));
      scene.add(devil);
      // the sky: a small, pale sun, two little moons, and drifting dust
      const sunDisk = sprite(glowTexture(), 9, { color: 0xfff6e8, fog: false }); sunDisk.position.set(40, 30, -90); scene.add(sunDisk);
      const moons = [[2.2, -36, 34, -80], [1.2, 26, 42, -90]].map(([r, x, y, z]) => {
        const m = new THREE.Mesh(new THREE.DodecahedronGeometry(r, 1), new THREE.MeshStandardMaterial({ color: 0x9a8a80, roughness: 1, flatShading: true, fog: false }));
        m.scale.set(1.2, .9, 1); m.position.set(x, y, z); scene.add(m); return m;
      });
      const dust = risingPoints(500, 60, -1.4, 8, 0xe8b88a, .09); dust.material.blending = THREE.NormalBlending; dust.material.opacity = .5; scene.add(dust);
      scene.userData.mars = { rover, wheels, devil, moons, dust };
    },
    tick(scene, t, dt, fx) {
      const m = scene.userData.mars;
      const a = t * .07, r = 15.5;
      m.rover.position.set(Math.cos(a) * r, -1.6, Math.sin(a) * r); m.rover.rotation.y = -a - Math.PI / 2;
      for (const w of m.wheels) w.rotation.y += dt * 2.5;
      const d = t * .03;
      m.devil.position.set(Math.cos(d) * 34, -1.6, -20 + Math.sin(d * 1.7) * 14); m.devil.rotation.y += dt * 3;
      m.moons[0].position.x = -36 + Math.sin(t * .05) * 8; m.moons[1].position.x = 26 - Math.sin(t * .02) * 5;
      const p = m.dust.geometry.attributes.position;
      for (let i = 0; i < p.count; i++) { let x = p.getX(i) + dt * (.6 + (i % 5) * .2); if (x > 30) x = -30; p.setX(i, x); }
      p.needsUpdate = true;
      every(scene, "beacon", 7, t, () => fx.ping(new THREE.Vector3(0, -1.55, 0), 0x3ee0ff, 13));
      every(scene, "orbiter", 9, t, () => {   // an orbiter crossing the sky
        const from = new THREE.Vector3(-70, 34 + Math.random() * 10, -70 + Math.random() * 20);
        fx.shootingStar(0xffffff, from, from.clone().add(new THREE.Vector3(140, 6, -10)), 9000);
      });
    },
    node(kind, color) {
      const g = new THREE.Group(), add = THREE.AdditiveBlending, white = mat(0xebe6dc), grey = mat(0x8a8580, { metalness: .3 });
      const glow = new THREE.MeshBasicMaterial({ color });
      const at = (m, x, y, z) => { m.position.set(x, y, z); m.castShadow = true; g.add(m); return m; };
      if (kind === "jarvis") {   // the command hub: a big dome with a lit band, an antenna mast and a turning dish
        at(new THREE.Mesh(new THREE.CylinderGeometry(1.95, 2.05, .36, 32), grey), 0, -1.42, 0);
        const shell = at(new THREE.Mesh(new THREE.SphereGeometry(1.75, 18, 9, 0, Math.PI * 2, 0, Math.PI / 2), white), 0, -1.24, 0);
        const frame = new THREE.LineSegments(new THREE.EdgesGeometry(shell.geometry, 20), new THREE.LineBasicMaterial({ color: 0x8a8580 })); frame.position.y = -1.24; g.add(frame);
        const band = new THREE.Mesh(new THREE.TorusGeometry(1.78, .07, 6, 64), glow); band.rotation.x = Math.PI / 2; band.position.y = -1.0; g.add(band);
        const lock = at(new THREE.Mesh(new THREE.CylinderGeometry(.42, .42, 1, 12), white), 0, -1.1, 1.9); lock.rotation.x = Math.PI / 2;
        at(new THREE.Mesh(new THREE.CylinderGeometry(.06, .08, 1.7, 8), grey), 0, 1.15, 0);
        const dishArm = new THREE.Group(); dishArm.position.y = 1.7; g.add(dishArm);
        const dish = new THREE.Mesh(new THREE.SphereGeometry(.75, 20, 8, 0, Math.PI * 2, 0, .95), new THREE.MeshStandardMaterial({ color: 0xf2f0ea, side: THREE.DoubleSide, roughness: .5 }));
        dish.rotation.x = Math.PI * .75; dish.position.set(0, .2, .2); dish.castShadow = true; dishArm.add(dish);
        const feed = new THREE.Mesh(new THREE.CylinderGeometry(.02, .02, .7, 6), grey); feed.rotation.x = Math.PI * .25; feed.position.set(0, .45, .45); dishArm.add(feed);
        const beacon = sprite(glowTexture(), 1.1, { color, blending: add }); beacon.position.y = 2.05; g.add(beacon);
        const halo = sprite(glowTexture(), 5.5, { color, opacity: .25, blending: add }); halo.position.y = -.6; g.add(halo);
        g.userData = { tint: [glow, beacon.material, halo.material], spin: [[dishArm, 0, .35, 0]], float: null, height: 3.9, ground: true, blink: [beacon.material] };
        return g;
      }
      if (kind === "user") {   // your rover, headlights in your colour
        at(new THREE.Mesh(new THREE.BoxGeometry(1.5, .4, .95), white), 0, -.92, 0);
        at(new THREE.Mesh(new THREE.BoxGeometry(1.6, .04, 1.1), [grey, grey, new THREE.MeshStandardMaterial({ map: solarTexture(), metalness: .4, roughness: .35 }), grey, grey, grey]), 0, -.7, 0);
        for (const x of [-.55, 0, .55]) for (const z of [-.56, .56]) { const w = at(new THREE.Mesh(new THREE.CylinderGeometry(.25, .25, .16, 14), mat(0x3a3430)), x, -1.35, z); w.rotation.x = Math.PI / 2; }
        at(new THREE.Mesh(new THREE.CylinderGeometry(.04, .04, .8, 6), grey), .55, -.3, .25);
        at(new THREE.Mesh(new THREE.BoxGeometry(.24, .16, .3), grey), .58, .14, .25);
        for (const z of [-.28, .28]) at(new THREE.Mesh(new THREE.SphereGeometry(.08, 10, 8), glow), .78, -.9, z);
        const beacon = sprite(glowTexture(), 1.1, { color, blending: add }); beacon.position.y = .55; g.add(beacon);
        g.userData = { tint: [glow, beacon.material], spin: [], float: null, height: 1.75, ground: true };
        return g;
      }
      // a lander pod: gold-foil body on four legs, a light ring in its status colour, a small dish on top
      const foil = mat(0xd6a83a, { metalness: .6, roughness: .35 });
      at(new THREE.Mesh(new THREE.CylinderGeometry(.55, .6, .6, 8), foil), 0, -.75, 0);
      at(new THREE.Mesh(new THREE.CylinderGeometry(.48, .55, .2, 8), white), 0, -.35, 0);
      for (let i = 0; i < 4; i++) {
        const a = i / 4 * Math.PI * 2 + Math.PI / 4, from = new THREE.Vector3(Math.cos(a) * .45, -.95, Math.sin(a) * .45), to = new THREE.Vector3(Math.cos(a) * .9, -1.56, Math.sin(a) * .9);
        const leg = new THREE.Mesh(new THREE.CylinderGeometry(.035, .035, from.distanceTo(to), 6), grey);
        leg.position.copy(from).add(to).multiplyScalar(.5); leg.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), to.clone().sub(from).normalize()); leg.castShadow = true; g.add(leg);
        at(new THREE.Mesh(new THREE.CylinderGeometry(.13, .15, .05, 10), grey), to.x, -1.58, to.z);
      }
      const ring = new THREE.Mesh(new THREE.TorusGeometry(.62, .05, 6, 32), glow); ring.rotation.x = Math.PI / 2; ring.position.y = -.6; g.add(ring);
      at(new THREE.Mesh(new THREE.CylinderGeometry(.03, .03, .5, 6), grey), .15, -.05, 0);
      const dish = at(new THREE.Mesh(new THREE.SphereGeometry(.22, 12, 6, 0, Math.PI * 2, 0, .9), new THREE.MeshStandardMaterial({ color: 0xf2f0ea, side: THREE.DoubleSide })), .15, .25, 0);
      dish.rotation.x = Math.PI * .8;
      const lamp = sprite(glowTexture(), 1.5, { color, blending: add }); lamp.position.y = -.6; g.add(lamp);
      const pad = new THREE.Mesh(new THREE.RingGeometry(1, 1.12, 40), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .75, side: THREE.DoubleSide }));
      pad.rotation.x = -Math.PI / 2; pad.position.y = -1.57; g.add(pad);
      g.userData = { tint: [glow, lamp.material, pad.material], spin: [], float: null, height: 1.55, ground: true };
      return g;
    },
    link: color => new THREE.LineDashedMaterial({ color, dashSize: .4, gapSize: .25, transparent: true, opacity: .8 }),
    particle(color) { return sprite(glowTexture(), .9, { color, blending: THREE.AdditiveBlending }); },
    burst(scene, at, fx) { fx.fountain(at, [0x6dff8a, 0xffffff], 18, 4); fx.ring(at, 0x6dff8a, 3); },
  },

  hud: {
    background: 0x01050a, bloom: [0.9, 0.5, 0.2], linkY: -.2, msg: 0xffc24a,
    status: { starting: 0x4fe3ff, working: 0x4fe3ff, waiting: 0xffc24a, "needs you": 0xff3d55, done: 0x5dffb0, stopped: 0x23475a, failed: 0xff3d55 },
    world(scene) {
      scene.fog = new THREE.FogExp2(0x01050a, 0.03);
      scene.add(new THREE.AmbientLight(0x335566, 0.5));
      const polar = new THREE.PolarGridHelper(16, 24, 10, 96, 0x1f9fc4, 0x0b3a4c); polar.position.y = -1.5; scene.add(polar);
      const grid = new THREE.GridHelper(70, 70, 0x082634, 0x082634); grid.position.y = -1.52; scene.add(grid);
      const dust = new THREE.BufferGeometry(), pts = [];
      for (let i = 0; i < 500; i++) pts.push((Math.random() - .5) * 40, Math.random() * 12 - 1, (Math.random() - .5) * 40);
      dust.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
      const motes = new THREE.Points(dust, new THREE.PointsMaterial({ color: 0x4fe3ff, size: .05, transparent: true, opacity: .6 }));
      motes.userData.drift = true; scene.add(motes);
      // great holographic rings turning around the whole room, and a scan ring sweeping up through it
      const rings = [[17, .4, .12], [20, -.25, -.08], [23, .1, .05]].map(([radius, tilt, speed]) => {
        const ring = new THREE.Mesh(new THREE.TorusGeometry(radius, .03, 6, 200), new THREE.MeshBasicMaterial({ color: 0x4fe3ff, transparent: true, opacity: .22 }));
        ring.rotation.set(Math.PI / 2 + tilt, 0, 0); ring.position.y = 2; ring.userData.speed = speed; scene.add(ring);
        for (let i = 0; i < 24; i++) {   // tick marks along each ring
          const tick = new THREE.Mesh(new THREE.BoxGeometry(.06, .5, .06), ring.material); const a = i / 24 * Math.PI * 2;
          tick.position.set(Math.cos(a) * radius, Math.sin(a) * radius, 0); tick.rotation.z = a; ring.add(tick);
        }
        return ring;
      });
      const scan = new THREE.Mesh(new THREE.TorusGeometry(15, .05, 6, 160), new THREE.MeshBasicMaterial({ color: 0x4fe3ff, transparent: true }));
      scan.rotation.x = Math.PI / 2; scene.add(scan);
      scene.userData.hud = { rings, scan };
    },
    tick(scene, t) {
      const hud = scene.userData.hud;
      for (const ring of hud.rings) ring.rotation.z += ring.userData.speed * .016;
      const k = (t % 7) / 7; hud.scan.position.y = -1.5 + k * 11; hud.scan.material.opacity = .55 * (1 - k);
    },
    node(kind, color) {   // an arc reactor: glowing core, rings turning on different axes, a light beam and a floor ring
      const g = new THREE.Group(), r = kind === "jarvis" ? 1.5 : kind === "user" ? .8 : 1;
      const core = new THREE.Mesh(new THREE.SphereGeometry(r * .32, 24, 16), new THREE.MeshBasicMaterial({ color }));
      const tint = [core.material], spin = [];
      [[r, .045, [Math.PI / 2, 0, 0], [0, 0, .6]], [r * .78, .03, [0, 0, 0], [.5, .3, 0]], [r * 1.18, .02, [Math.PI / 3, Math.PI / 4, 0], [-.3, .45, 0]]]
        .forEach(([radius, tube, rot, speed]) => {
          const ring = new THREE.Mesh(new THREE.TorusGeometry(radius, tube, 8, 96), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .9 }));
          ring.rotation.set(...rot); g.add(ring); tint.push(ring.material); spin.push([ring, ...speed]);
        });
      const floor = new THREE.Mesh(new THREE.RingGeometry(r * .9, r * 1.05, 64), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .5, side: THREE.DoubleSide }));
      floor.rotation.x = -Math.PI / 2; floor.position.y = -1.48; g.add(floor); tint.push(floor.material);
      const beam = new THREE.Mesh(new THREE.CylinderGeometry(.04, .04, 1.4, 8), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .35 }));
      beam.position.y = -.8; g.add(beam, core); tint.push(beam.material);
      g.userData = { tint, spin, float: null, height: r * 1.6, flicker: beam.material };
      return g;
    },
    link: color => new THREE.LineDashedMaterial({ color, dashSize: .3, gapSize: .2, transparent: true, opacity: .6 }),
    particle(color) { return sprite(glowTexture(), .7, { color, blending: THREE.AdditiveBlending }); },
    burst(scene, at, fx) { fx.ring(at, 0x5dffb0, 3.5); setTimeout(() => fx.ring(at, 0x4fe3ff, 2.5), 180); },
  },

  village: {
    background: 0x8fd6ff, bloom: null, shadows: true, msg: 0xffd84a, home: [[0, 12.5, 17], [0, -1, 1.2]],
    status: { starting: 0xffcf3a, working: 0xffb02e, waiting: 0x6fb8ff, "needs you": 0xff4b3a, done: 0x6fd43a, stopped: 0x9a9a9a, failed: 0xff4b3a },
    world(scene, fx) {
      scene.fog = new THREE.Fog(0x8fd6ff, 35, 80);
      scene.add(new THREE.HemisphereLight(0xdff3ff, 0x5a8a3a, 1.1));
      const sun = new THREE.DirectionalLight(0xfff1d0, 2.2); sun.position.set(10, 18, 8); sun.castShadow = true;
      sun.shadow.mapSize.set(2048, 2048); Object.assign(sun.shadow.camera, { left: -18, right: 18, top: 18, bottom: -18 });
      scene.add(sun);
      // the island: grassy top, sandy sides, in gently moving water
      const island = new THREE.Mesh(new THREE.CylinderGeometry(12, 13, 1.4, 48),
        [mat(0xe8d39a), mat(0x7cc94a), mat(0xd9bf7c)]);
      island.position.y = -2.2; island.receiveShadow = true; scene.add(island);
      const water = new THREE.Mesh(new THREE.PlaneGeometry(160, 160, 80, 80),
        new THREE.MeshStandardMaterial({ color: 0x2f9ad6, roughness: .35, metalness: .1, flatShading: true }));
      water.rotation.x = -Math.PI / 2; water.position.y = -2.6; water.userData.waves = true; scene.add(water);
      // trees around the edge, keeping clear of where buildings go
      for (let i = 0; i < 26; i++) {
        const a = Math.random() * Math.PI * 2, d = 10.4 + Math.random() * 1.3;   // round the edge, clear of the huts
        const tree = new THREE.Group();
        const trunk = new THREE.Mesh(new THREE.CylinderGeometry(.12, .16, .7, 6), mat(0x7a4a22)); trunk.position.y = .35;
        const leaves = new THREE.Mesh(new THREE.ConeGeometry(.55 + Math.random() * .2, 1.4, 7), mat(0x3f9a3a)); leaves.position.y = 1.2;
        tree.add(trunk, leaves); tree.position.set(Math.cos(a) * d, -1.5, Math.sin(a) * d);
        tree.traverse(o => { if (o.isMesh) o.castShadow = true; }); scene.add(tree);
      }
      for (let i = 0; i < 7; i++) {   // clouds
        const cloud = new THREE.Group();
        for (let j = 0; j < 4; j++) {
          const puff = new THREE.Mesh(new THREE.SphereGeometry(1 + Math.random() * .8, 10, 8), mat(0xffffff, { roughness: 1 }));
          puff.position.set(j * 1.3 - 2, Math.random() * .5, Math.random() * .8); cloud.add(puff);
        }
        cloud.position.set((Math.random() - .5) * 90, 15 + Math.random() * 5, -32 - Math.random() * 35);   // high up and behind
        cloud.userData.cloud = .3 + Math.random() * .5; scene.add(cloud);
      }
      // a windmill on its own little islet, its sails turning
      const islet = new THREE.Mesh(new THREE.CylinderGeometry(2.2, 2.6, 1, 24), [mat(0xe8d39a), mat(0x7cc94a), mat(0xd9bf7c)]);
      islet.position.set(15, -2.3, -9); scene.add(islet);
      const mill = new THREE.Group(); mill.position.set(15, -1.8, -9);
      const tower = new THREE.Mesh(new THREE.CylinderGeometry(.55, .85, 3.2, 8), mat(0xf2e6cc)); tower.position.y = 1.6;
      const cap = new THREE.Mesh(new THREE.ConeGeometry(.75, .9, 8), mat(0xd6402f)); cap.position.y = 3.6;
      const sails = new THREE.Group(); sails.position.set(0, 3, .75);
      for (let i = 0; i < 4; i++) {
        const arm = new THREE.Group(); arm.rotation.z = i * Math.PI / 2;
        const sail = new THREE.Mesh(new THREE.BoxGeometry(.4, 1.9, .04), mat(0xfff8e6)); sail.position.y = 1.05; arm.add(sail); sails.add(arm);
      }
      mill.add(tower, cap, sails); mill.rotation.y = -.9; mill.traverse(o => { if (o.isMesh) o.castShadow = true; }); scene.add(mill);
      const birds = Array.from({ length: 7 }, (_, i) => {   // a flock circling overhead
        const bird = new THREE.Group(), wing = new THREE.BoxGeometry(.5, .03, .14), dark = mat(0x3a2a20);
        const left = new THREE.Mesh(wing, dark), right = new THREE.Mesh(wing, dark);
        left.position.x = -.24; right.position.x = .24; bird.add(left, right);
        bird.userData = { left, right, a: i * .5, r: 11 + (i % 3), y: 7 + (i % 2) }; scene.add(bird); return bird;
      });
      const boat = new THREE.Group();   // a little sailboat going round the island
      const hullGeo = new THREE.BoxGeometry(1.4, .35, .5); const hull = new THREE.Mesh(hullGeo, mat(0x8a5523));
      const mast = new THREE.Mesh(new THREE.CylinderGeometry(.03, .03, 1.5, 6), mat(0x5a3410)); mast.position.y = .9;
      const sail = new THREE.Mesh(new THREE.ConeGeometry(.55, 1.2, 3), mat(0xfff8e6)); sail.position.set(.15, 1, 0); sail.scale.z = .15;
      boat.add(hull, mast, sail); scene.add(boat);
      scene.userData.village = { sails, birds, boat };
    },
    tick(scene, t) {
      const v = scene.userData.village;
      v.sails.rotation.z = -t * .9;
      for (const b of v.birds) {
        const a = b.userData.a + t * .25;
        b.position.set(Math.cos(a) * b.userData.r, b.userData.y + Math.sin(t * 2 + b.userData.a) * .3, Math.sin(a) * b.userData.r);
        b.rotation.y = -a; const flap = Math.sin(t * 9 + b.userData.a * 3) * .5;
        b.userData.left.rotation.z = flap; b.userData.right.rotation.z = -flap;
      }
      const a = -t * .06;
      v.boat.position.set(Math.cos(a) * 17, -2.45 + Math.sin(t * 1.4) * .08, Math.sin(a) * 17);
      v.boat.rotation.set(Math.sin(t * 1.1) * .06, -a + Math.PI, Math.sin(t * 1.3) * .05);
    },
    node(kind, color) {
      const g = new THREE.Group(); let tint = [], height = 2;
      const box = (w, h, d, c, x = 0, y = 0, z = 0) => {
        const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat(c)); m.position.set(x, y, z); g.add(m); return m;
      };
      if (kind === "jarvis") {          // Town Hall: stone base, timber hall, red roof, flag
        box(3.4, .8, 3, 0x9a9a9a, 0, -1.1);
        box(2.8, 1.6, 2.4, 0xc98a46, 0, -.0);
        const roof = new THREE.Mesh(new THREE.ConeGeometry(2.3, 1.6, 4), mat(0xd6402f)); roof.position.y = 1.6; roof.rotation.y = Math.PI / 4; g.add(roof);
        box(.6, .9, .05, 0x5a3410, 0, -.4, 1.23);
        for (const x of [-.85, .85]) box(.5, .45, .05, 0xffe8a3, x, .2, 1.23);
        const pole = new THREE.Mesh(new THREE.CylinderGeometry(.04, .04, 1.4, 6), mat(0x5a3410)); pole.position.y = 3.1; g.add(pole);
        const flag = new THREE.Mesh(new THREE.PlaneGeometry(.8, .45), mat(0xffcf3a, { side: THREE.DoubleSide })); flag.position.set(.42, 3.55, 0); g.add(flag);
        g.userData.flag = flag; height = 4.3;
      } else if (kind === "user") {     // your tower with a crown
        const tower = new THREE.Mesh(new THREE.CylinderGeometry(.75, .85, 3.2, 10), mat(0xb0b0b0)); tower.position.y = .1; g.add(tower);
        for (let i = 0; i < 8; i++) {
          const a = i / 8 * Math.PI * 2; box(.28, .35, .28, 0xa0a0a0, Math.cos(a) * .7, 1.85, Math.sin(a) * .7);
        }
        const crown = new THREE.Mesh(new THREE.ConeGeometry(.45, .6, 5), mat(0xffcf3a, { metalness: .6, roughness: .3 })); crown.position.y = 2.4; g.add(crown);
        box(.5, .8, .05, 0x4a2a0c, 0, -1, .8); height = 3.4;
      } else {                          // a builder's hut: roof in the status colour, chimney smoke while working
        box(1.6, 1.1, 1.4, 0xd39b5a, 0, -.95);
        const roof = new THREE.Mesh(new THREE.ConeGeometry(1.35, .95, 4), mat(color)); roof.position.y = .07; roof.rotation.y = Math.PI / 4; g.add(roof);
        box(.45, .7, .05, 0x5a3410, 0, -1.1, .72);
        const chimney = box(.25, .6, .25, 0x8a8a8a, .45, .2, -.2);
        tint = [roof.material]; g.userData.chimney = chimney; height = 2.6;
      }
      g.traverse(o => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
      g.userData = { ...g.userData, tint, spin: [], float: null, height, ground: true };
      return g;
    },
    link: () => new THREE.MeshStandardMaterial({ color: 0xd9bd82, roughness: 1 }),
    particle(color, kind) { return sprite(emojiTexture(kind === "result" ? "💰" : kind === "user" ? "📯" : "📜"), 1.1); },
    burst(scene, at, fx) { fx.coins(at); },
  },
};

// ---------- the scene ----------
export function createScene(container, hooks) {
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  container.appendChild(renderer.domElement);
  const labels = new CSS2DRenderer();
  Object.assign(labels.domElement.style, { position: "absolute", inset: "0", pointerEvents: "none" });
  container.appendChild(labels.domElement);

  const camera = new THREE.PerspectiveCamera(45, 1, .1, 500);
  let homeBase = [[0, 8, 12], [0, -.5, 1.2]];
  const HOME = { pos: new THREE.Vector3(0, 10, 15), target: new THREE.Vector3(0, -.5, 1.5) };
  camera.position.copy(HOME.pos);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.copy(HOME.target); controls.enableDamping = true; controls.dampingFactor = .07;
  controls.maxPolarAngle = Math.PI * .47; controls.minDistance = 5; controls.maxDistance = 40;
  controls.autoRotate = true; controls.autoRotateSpeed = .35;

  let scene, composer, theme, themeKey;
  const nodes = {};            // id -> { group, pick, label, kind, status, spawnAt, info }
  const links = {};            // id -> line/mesh from Jarvis
  const effects = [];          // things animating: (t, dt) => keep?
  let flyTo = null;

  // ---- effects shared by themes ----
  const fx = {
    ring(at, color, size) {
      const ring = new THREE.Mesh(new THREE.TorusGeometry(.6, .05, 8, 64), new THREE.MeshBasicMaterial({ color, transparent: true }));
      ring.position.copy(at); ring.rotation.x = Math.PI / 2; scene.add(ring);
      const start = performance.now();
      effects.push(now => {
        const k = Math.min(1, (now - start) / 900);
        ring.scale.setScalar(1 + k * size); ring.material.opacity = 1 - k;
        if (k >= 1) { scene.remove(ring); return false; } return true;
      });
    },
    coins(at) {
      for (let i = 0; i < 12; i++) {
        const coin = sprite(emojiTexture("🪙"), .55); coin.position.copy(at); scene.add(coin);
        const a = Math.random() * Math.PI * 2, v = new THREE.Vector3(Math.cos(a) * 2.2, 4 + Math.random() * 2, Math.sin(a) * 2.2), start = performance.now();
        effects.push(now => {
          const s = (now - start) / 1000;
          coin.position.set(at.x + v.x * s, at.y + v.y * s - 6 * s * s, at.z + v.z * s);
          coin.material.opacity = Math.max(0, 1 - s / 1.1);
          if (s > 1.1) { scene.remove(coin); return false; } return true;
        });
      }
    },
    smoke(at) {
      const puff = new THREE.Mesh(new THREE.SphereGeometry(.18, 8, 6), new THREE.MeshStandardMaterial({ color: 0xeeeeee, transparent: true, opacity: .8, flatShading: true }));
      puff.position.copy(at); scene.add(puff);
      const start = performance.now(), drift = (Math.random() - .5) * .3;
      effects.push(now => {
        const s = (now - start) / 1000;
        puff.position.set(at.x + drift * s, at.y + s * .9, at.z); puff.scale.setScalar(1 + s * 1.4); puff.material.opacity = .8 * (1 - s / 2);
        if (s > 2) { scene.remove(puff); return false; } return true;
      });
    },
    ping(at, color, radius, ms = 2600) {   // a thin ring spreading out across the floor
      const m = new THREE.Mesh(new THREE.RingGeometry(.97, 1, 128), new THREE.MeshBasicMaterial({ color, transparent: true,
        depthWrite: false, side: THREE.DoubleSide, blending: THREE.AdditiveBlending }));
      m.rotation.x = -Math.PI / 2; m.position.copy(at); scene.add(m);
      const start = performance.now();
      effects.push(now => {
        const k = Math.min(1, (now - start) / ms);
        m.scale.setScalar(.5 + k * radius); m.material.opacity = .8 * (1 - k);
        if (k >= 1) { scene.remove(m); m.geometry.dispose(); m.material.dispose(); return false; } return true;
      });
    },
    shootingStar(color, from, to, ms) {   // a bright head with a fading tail
      const head = sprite(glowTexture(), 1.1, { color, blending: THREE.AdditiveBlending, fog: false });
      const tail = new THREE.Line(new THREE.BufferGeometry().setFromPoints([from, from]),
        new THREE.LineBasicMaterial({ color, transparent: true, fog: false, blending: THREE.AdditiveBlending }));
      scene.add(head, tail);
      const start = performance.now();
      effects.push(now => {
        const k = Math.min(1, (now - start) / ms), at = from.clone().lerp(to, k), back = from.clone().lerp(to, Math.max(0, k - .3));
        head.position.copy(at); tail.geometry.setFromPoints([back, at]);
        head.material.opacity = tail.material.opacity = 1 - k * k;
        if (k >= 1) { scene.remove(head, tail); tail.geometry.dispose(); return false; } return true;
      });
    },
    fountain(at, colors, count, height) {   // sparks thrown up that fall back down
      for (let i = 0; i < count; i++) {
        const p = sprite(glowTexture(), .3 + Math.random() * .3, { color: colors[i % colors.length], blending: THREE.AdditiveBlending });
        p.position.copy(at); scene.add(p);
        const a = Math.random() * Math.PI * 2, out = Math.random() * 1.4, up = height * (.6 + Math.random() * .5), start = performance.now();
        effects.push(now => {
          const s = (now - start) / 1000;
          p.position.set(at.x + Math.cos(a) * out * s, at.y + up * s - 4.9 * s * s, at.z + Math.sin(a) * out * s);
          p.material.opacity = Math.max(0, 1 - s / 1.6);
          if (s > 1.6 || p.position.y < at.y - .2) { scene.remove(p); return false; } return true;
        });
      }
      fx.ping(at.clone().setY(at.y + .05), colors[0], 2.2, 900);
    },
    spawn(at, color) {
      fx.ring(at.clone().setY(-1.4), color, 2.5);
      for (let i = 0; i < 16; i++) {
        const p = sprite(glowTexture(), .35, { color, blending: THREE.AdditiveBlending }); p.position.copy(at); scene.add(p);
        const a = Math.random() * Math.PI * 2, r = .5 + Math.random() * 1.5, start = performance.now();
        effects.push(now => {
          const k = Math.min(1, (now - start) / 1000);
          p.position.set(at.x + Math.cos(a) * r * k, at.y + 2.5 * k, at.z + Math.sin(a) * r * k); p.material.opacity = 1 - k;
          if (k >= 1) { scene.remove(p); return false; } return true;
        });
      }
    },
  };

  // ---- layout: Jarvis in the middle and you behind him. The crew working on tasks stand in an arc in front, the
  // standing specialists in an arc behind, either side of you. The middle of each arc stays clear, so nothing stands
  // between you and Jarvis or right in front of him.
  function arc(ids, [from, to], gap, rx, rz) {
    const mid = (from + to) / 2, span = to - from - gap, out = {};
    ids.forEach((id, i) => {
      let deg = ids.length === 1 ? from + span * .7 : from + (span * i) / (ids.length - 1);
      if (deg > mid - gap / 2) deg += gap;
      const a = deg * Math.PI / 180, grow = 1 + Math.max(0, ids.length - 5) * .09;   // a bigger crew, a wider arc
      out[id] = new THREE.Vector3(Math.cos(a) * rx * grow, 0, Math.sin(a) * rz * grow);
    });
    return out;
  }
  function layout() {
    const pos = { jarvis: new THREE.Vector3(0, 0, 0), user: new THREE.Vector3(0, 0, -6.5) };
    const ids = Object.keys(nodes).filter(id => nodes[id].kind === "agent");
    const standing = ids.filter(id => nodes[id].info?.kind === "standing"), crew = ids.filter(id => !standing.includes(id));
    Object.assign(pos, arc(crew, [8, 172], 30, 7.4, 6.2), arc(standing, [210, 330], 36, 9.4, 8.2));
    return pos;
  }
  // back the camera off when the team spreads wider than the view
  let fit = 1, userMoved = false;
  function fitTo(pos) {
    const spread = Math.max(7, ...Object.values(pos).map(p => Math.abs(p.x) * 1.15), ...Object.values(pos).map(p => Math.abs(p.z) * 1.4));
    const want = Math.min(1.45, Math.max(1, spread / 8.2));
    if (Math.abs(want - fit) < .04) return;
    fit = want; resize();
    if (!userMoved) tweenCamera(HOME.pos.clone(), HOME.target.clone());
  }

  function colorFor(status) { return theme.status[status] ?? theme.status.working; }

  function makeLabel(id) {
    const div = document.createElement("div"); div.className = "lbl3d";
    const obj = new CSS2DObject(div); return obj;
  }

  function addNode(id, kind, info) {
    const status = kind === "agent" ? info.status : kind === "jarvis" ? "working" : "waiting";
    const group = theme.node(kind, colorFor(status), info);
    const pick = new THREE.Mesh(new THREE.SphereGeometry(group.userData.height * .62, 12, 8), new THREE.MeshBasicMaterial({ visible: false }));
    pick.userData.nodeId = id; group.add(pick);
    const label = makeLabel(id); label.position.y = group.userData.height + .2; group.add(label);
    const aura = kind === "agent" ? makeAura(group.userData.height, colorFor(status)) : null;
    if (aura) group.add(aura);
    scene.add(group);
    nodes[id] = { group, pick, label, kind, status, info, aura, auraLevel: 0, spawnAt: kind === "agent" ? performance.now() : null, base: new THREE.Vector3() };
    if (kind === "agent") {
      const line = makeLink(id); links[id] = line; scene.add(line);
    }
    updateLabel(id);
    return nodes[id];
  }

  // While an agent works: a ring spins and pulses around its base and three sparks circle it, in the status colour,
  // the same in every theme, so who's busy is clear at a glance.
  function makeAura(height, color) {
    // glowing (added light) on dark themes; solid on light ones, where added light barely shows
    const light = theme.matte || themeKey === "village";
    const aura = new THREE.Group(), r = Math.max(1.05, height * .62), add = light ? THREE.NormalBlending : THREE.AdditiveBlending;
    const ring = new THREE.Mesh(new THREE.RingGeometry(r, r + .09, 64, 1, 0, Math.PI * 1.6),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false, blending: add }));
    ring.rotation.x = -Math.PI / 2;
    const glow = new THREE.Mesh(new THREE.RingGeometry(r - .2, r + .3, 64),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false, blending: add }));
    glow.rotation.x = -Math.PI / 2;
    const sparks = [0, 1, 2].map(() => sprite(glowTexture(), .55, { color, opacity: 0, blending: add }));
    aura.add(ring, glow, ...sparks);
    aura.position.y = -1.47;
    aura.userData = { ring, glow, sparks, r, height };
    aura.visible = false;
    return aura;
  }

  function makeLink(id) {
    const color = colorFor(nodes[id]?.status || "working");
    if (themeKey === "village") {   // a dirt path on the grass
      const m = new THREE.Mesh(new THREE.PlaneGeometry(1, .8), theme.link(color));
      m.rotation.x = -Math.PI / 2; m.receiveShadow = true; m.userData.path = true; return m;
    }
    const geo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, 0, 1)]);
    const line = new THREE.Line(geo, theme.link(color)); return line;
  }

  function placeLink(id, a, b) {
    const link = links[id]; if (!link) return;
    if (link.userData.path) {
      const mid = a.clone().add(b).multiplyScalar(.5), len = a.distanceTo(b);
      link.position.set(mid.x, -1.49, mid.z); link.scale.set(len, 1, 1);
      link.rotation.z = 0; link.rotation.set(-Math.PI / 2, 0, -Math.atan2(b.z - a.z, b.x - a.x));
      return;
    }
    const y = theme.linkY ?? 0;
    link.geometry.setFromPoints([a.clone().setY(y), b.clone().setY(y)]);
    if (link.computeLineDistances) link.computeLineDistances();
  }

  function updateLabel(id) {
    const n = nodes[id]; if (!n) return;
    const name = n.kind === "agent" ? n.info.name : n.kind === "jarvis" ? hooks.words().jarvis : hooks.words().user;
    const sub = n.kind === "agent" ? hooks.statusWord(n.status) : "";
    const doing = n.kind === "agent" && !FINISHED.includes(n.status) ? (hooks.activityOf(id) || "") : "";
    const icon = themeKey === "village" && n.kind === "agent"
      ? ({ starting: "🧳", working: "🔨", waiting: "💤", "needs you": "❗", done: "💰", stopped: "🏳️", failed: "💥" }[n.status] || "🔨") : "";
    const badge = themeKey === "village" && n.kind === "agent" ? hooks.roleIcon(n.info) : "";
    const star = n.kind === "agent" && n.info?.kind === "standing" ? `<span class="star" title="Always on">★</span>` : "";
    const working = n.kind === "agent" && (n.status === "working" || n.status === "starting");
    n.label.element.classList.toggle("working", working);
    n.label.element.classList.toggle("idle", n.kind === "agent" && n.status === "waiting");
    n.label.element.innerHTML =
      `${icon ? `<div class="bubble">${icon}</div>` : ""}<div class="name">${star}${badge ? `<span class="badge">${badge}</span>` : ""}${escapeHtml(name)}</div>` +
      (sub ? `<div class="state" style="--c:#${new THREE.Color(colorFor(n.status)).getHexString()}">${working ? '<span class="cog">⚙</span>' : ""}${escapeHtml(sub)}</div>` : "") +
      (doing ? `<div class="doing">${escapeHtml(doing.length > 30 ? doing.slice(0, 29) + "…" : doing)}</div>` : "");
  }

  function recolor(id) {
    const n = nodes[id]; const c = new THREE.Color(colorFor(n.status));
    for (const m of n.group.userData.tint || []) {
      if (m.emissive) m.emissive.copy(c);
      if (!m.userData?.keepColor) m.color.copy(c);
    }
    const dim = n.status === "stopped" || n.status === "failed";
    n.group.traverse(o => {
      if (o.isMesh && o !== n.pick && o.material && !Array.isArray(o.material)) {
        o.material.transparent = true;
        o.material.opacity = dim ? .35 : (o.material.userData.baseOpacity ??= o.material.opacity);
      }
    });
    if (links[id] && links[id].material.color) links[id].material.color.copy(themeKey === "village" ? new THREE.Color(0xd9bd82) : c);
    if (n.aura) {
      const u = n.aura.userData;
      for (const m of [u.ring.material, u.glow.material, ...u.sparks.map(s => s.material)]) m.color.copy(c);
    }
  }

  // ---- building a theme ----
  function build(key) {
    if (scene) {
      for (const n of Object.values(nodes)) n.label.element.remove();
      for (const tag of labels.domElement.querySelectorAll(".msg3d")) tag.remove();
      scene.traverse(o => { o.geometry?.dispose(); (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => m?.dispose()); });
    }
    for (const k of Object.keys(nodes)) delete nodes[k];
    for (const k of Object.keys(links)) delete links[k];
    for (const k of Object.keys(threads)) delete threads[k];
    for (const k of Object.keys(tagShown)) delete tagShown[k];
    effects.length = 0;
    themeKey = THEMES[key] ? key : "command"; theme = THEMES[themeKey];
    scene = new THREE.Scene(); scene.background = new THREE.Color(theme.background);
    theme.world(scene, fx);
    renderer.toneMapping = themeKey === "village" ? THREE.ACESFilmicToneMapping : THREE.NoToneMapping;
    composer = null;
    if (theme.bloom) {
      composer = new EffectComposer(renderer);
      composer.addPass(new RenderPass(scene, camera));
      composer.addPass(new UnrealBloomPass(new THREE.Vector2(256, 256), ...theme.bloom));
      composer.addPass(new OutputPass());
    }
    addNode("jarvis", "jarvis", {}); addNode("user", "user", {});
    homeBase = theme.home || [[0, 8, 12], [0, -.5, 1.2]];   // each theme's starting view (for a wide window)
    resize();
    camera.position.copy(HOME.pos); controls.target.copy(HOME.target);
  }

  // ---- interaction: hover, click, right-click, double-click ----
  const ray = new THREE.Raycaster(), mouse = new THREE.Vector2();
  let hovered = null, downAt = null;
  function pickAt(ev) {
    const r = renderer.domElement.getBoundingClientRect();
    mouse.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(mouse, camera);
    const hit = ray.intersectObjects(Object.values(nodes).map(n => n.pick), false)[0];
    return hit ? hit.object.userData.nodeId : null;
  }
  renderer.domElement.addEventListener("pointermove", ev => {
    const id = pickAt(ev);
    if (id !== hovered) { hovered = id; renderer.domElement.style.cursor = id ? "pointer" : ""; }
    hooks.onHover(id, ev.clientX, ev.clientY);
  });
  renderer.domElement.addEventListener("pointerleave", () => { hovered = null; hooks.onHover(null); });
  renderer.domElement.addEventListener("pointerdown", ev => { downAt = [ev.clientX, ev.clientY]; controls.autoRotate = false; userMoved = true; });
  renderer.domElement.addEventListener("wheel", () => { userMoved = true; }, { passive: true });
  renderer.domElement.addEventListener("pointerup", ev => {
    if (!downAt || Math.hypot(ev.clientX - downAt[0], ev.clientY - downAt[1]) > 5 || ev.button !== 0) return;
    const id = pickAt(ev);
    if (!id) return;
    if (nodes[id].kind === "agent") { focus(id); hooks.onSelect(id); }
    else if (id === "jarvis") hooks.onJarvis();
    else hooks.onUser();
  });
  renderer.domElement.addEventListener("dblclick", ev => { if (!pickAt(ev)) reset(); });
  renderer.domElement.addEventListener("contextmenu", ev => {
    const id = pickAt(ev);
    if (id && nodes[id].kind === "agent") { ev.preventDefault(); hooks.onContext(id, ev.clientX, ev.clientY); }
  });

  function tweenCamera(pos, target) {
    flyTo = { fromPos: camera.position.clone(), fromTarget: controls.target.clone(), pos, target, start: performance.now() };
  }
  function focus(id) {
    const n = nodes[id]; if (!n) return;
    const p = n.group.position.clone();
    const dir = p.clone().setY(0).normalize(); if (dir.lengthSq() === 0) dir.set(0, 0, 1);
    tweenCamera(p.clone().add(dir.multiplyScalar(6)).add(new THREE.Vector3(0, 4, 0)), p.clone().setY(.3));
  }
  function reset() { tweenCamera(HOME.pos.clone(), HOME.target.clone()); }

  // ---- data from the page ----
  function sync(agents) {
    for (const [id, a] of Object.entries(agents)) {
      const n = nodes[id] || addNode(id, "agent", a.info);
      n.info = a.info;
      if (n.status !== a.info.status) {
        const was = n.status; n.status = a.info.status; recolor(id);
        if (a.info.status === "done" && was !== "done") theme.burst(scene, n.group.position.clone().setY(1.5), fx);
      }
      updateLabel(id);
    }
    for (const id of Object.keys(nodes)) if (nodes[id].kind === "agent" && !agents[id]) {
      scene.remove(nodes[id].group); nodes[id].label.element.remove(); scene.remove(links[id]); delete nodes[id]; delete links[id];
    }
    updateLabel("jarvis"); updateLabel("user");
    const pos = layout(); fitTo(pos);
    for (const [id, n] of Object.entries(nodes)) {
      n.base.copy(pos[id]);
      if (!n.placed) { n.group.position.copy(pos[id]); n.placed = true; if (n.spawnAt) fx.spawn(pos[id].clone(), colorFor(n.status)); }
    }
  }

  // ---- a message: a beam of light draws itself from sender to receiver, carrying a preview of what was said ----
  const threads = {};          // "a|b" -> { line, a, b, last }: a faint line left between agents who have been talking
  const tagShown = {};         // "a|b" -> the message text showing over that pair's beam (a newer one replaces it)
  function arcBetween(a, b) {
    const A = a.group.position.clone().setY(a.group.userData.height * .55), B = b.group.position.clone().setY(b.group.userData.height * .55);
    const mid = A.clone().add(B).multiplyScalar(.5); mid.y += A.distanceTo(B) * (themeKey === "village" ? .45 : .3);
    return new THREE.QuadraticBezierCurve3(A, mid, B);
  }
  function message(from, to, kind, text) {
    const a = nodes[from], b = nodes[to]; if (!a || !b || from === to) return;
    const color = kind === "result" ? theme.status.done : to === "user" ? theme.status["needs you"] : kind === "user" ? theme.status.waiting : (theme.msg ?? 0xa98bff);
    const glowy = themeKey !== "village" && !theme.matte, start = performance.now(), dur = themeKey === "village" ? 1700 : 1200;
    const curve = arcBetween(a, b), A = curve.v0, B = curve.v2;
    a.talkAt = start; fx.ring(A.clone().setY(-1.3), color, 1);
    // the beam: a bright core inside a wide soft glow, revealed along the arc as the message travels
    const SEG = 64, RAD = 8;
    const tube = (radius, opacity) => {
      const m = new THREE.Mesh(new THREE.TubeGeometry(curve, SEG, radius, RAD, false), new THREE.MeshBasicMaterial({
        color, transparent: true, opacity, depthWrite: false, fog: false, blending: glowy ? THREE.AdditiveBlending : THREE.NormalBlending }));
      m.geometry.setDrawRange(0, 0); scene.add(m); return m;
    };
    const haloOpacity = glowy ? .28 : .35, core = tube(glowy ? .05 : .08, 1), halo = tube(glowy ? .17 : .15, haloOpacity);
    const p = theme.particle(color, kind); scene.add(p);
    const trail = glowy ? Array.from({ length: 10 }, (_, i) => {
      const s = sprite(glowTexture(), .5 - i * .035, { color, blending: THREE.AdditiveBlending, opacity: .5 - i * .045 }); scene.add(s); return s;
    }) : [];
    let tag = null;   // what was said, floating above the middle of the beam
    const pair = [from, to].sort().join("|");
    if (text) {
      if (tagShown[pair]) { scene.remove(tagShown[pair]); tagShown[pair].element.remove(); }
      const div = document.createElement("div"); div.className = "msg3d";
      div.textContent = text.length > 70 ? text.slice(0, 68) + "…" : text;
      div.style.setProperty("--c", "#" + new THREE.Color(color).getHexString());
      tag = new CSS2DObject(div); scene.add(tag); tagShown[pair] = tag;
    }
    // between two agents (not via Jarvis), leave a thread that stays lit for a while
    if (a.kind === "agent" && b.kind === "agent") {
      const key = [from, to].sort().join("|");
      if (!threads[key]) {
        const line = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color, transparent: true, depthWrite: false,
          blending: glowy ? THREE.AdditiveBlending : THREE.NormalBlending }));
        scene.add(line); threads[key] = { line, a: from, b: to };
      }
      threads[key].last = start + dur;
    }
    for (const id of [from, to]) if (links[id]) links[id].userData.flash = start + dur;   // its line to Jarvis flares too
    const history = []; let arrived = 0;
    effects.push(now => {
      const k = Math.min(1, (now - start) / dur), e = ease(k), shown = Math.ceil(e * SEG) * RAD * 6;
      core.geometry.setDrawRange(0, shown); halo.geometry.setDrawRange(0, shown);
      if (k < 1) {
        const at = curve.getPoint(e);
        p.position.copy(at); history.unshift(at.clone()); history.length = Math.min(history.length, 30);
        trail.forEach((s, i) => s.position.copy(history[Math.min(history.length - 1, i * 3)]));
        if (tag) { const over = curve.getPoint(.35 + .3 * e); tag.position.copy(over).setY(over.y + 1.1); }
        return true;
      }
      if (!arrived) {
        arrived = now; b.talkAt = now; scene.remove(p); trail.forEach(s => scene.remove(s));
        if (kind === "result") theme.burst(scene, B.clone(), fx); else fx.ring(B.clone().setY(-1.3), color, 1.4);
      }
      const fade = Math.max(0, Math.min(1, 1 - (now - arrived - 1100) / 1600));   // hold lit, then fade out
      core.material.opacity = fade; halo.material.opacity = fade * haloOpacity;
      if (tag) tag.element.style.opacity = fade;
      if (fade > 0) return true;
      for (const m of [core, halo]) { scene.remove(m); m.geometry.dispose(); m.material.dispose(); }
      if (tag) { scene.remove(tag); if (tagShown[pair] === tag) delete tagShown[pair]; }
      return false;
    });
  }

  // ---- labels never sit on top of each other: the one further back (higher on screen) moves up out of the way,
  // and message texts give way to names. Measured a few times a second, applied every frame.
  const lift = new Map();
  let declutterAt = 0;
  function declutter(now) {
    const names = Object.values(nodes).map(n => n.label.element).filter(e => e.isConnected);
    const said = [...labels.domElement.querySelectorAll(".msg3d")];
    if (now > declutterAt) {
      declutterAt = now + 150;
      const box = e => { const r = e.getBoundingClientRect(); return { e, l: r.left, r: r.right, t: r.top, b: r.bottom }; };
      const order = [...names.map(box).sort((a, b) => b.b - a.b), ...said.map(box)];   // nearest names keep their place
      const placed = [], ceiling = container.getBoundingClientRect().top + 46;   // never up under the roster
      for (const it of order) {
        let shift = 0;
        for (let tries = 0; tries < 8; tries++) {
          const hit = placed.find(p => it.l < p.r - 2 && it.r > p.l + 2 && it.t - shift < p.b && it.b - shift > p.t);
          if (!hit) break;
          shift = Math.max(0, Math.min(140, it.b - hit.t + 3, it.t - ceiling));
        }
        lift.set(it.e, shift); placed.push({ ...it, t: it.t - shift, b: it.b - shift });
      }
    }
    for (const e of [...names, ...said]) { const s = lift.get(e); if (s) e.style.transform += ` translateY(${-s}px)`; }
  }

  // ---- the loop ----
  const clock = new THREE.Clock();
  let smokeTimer = 0;
  function frame() {
    const dt = clock.getDelta(), t = clock.elapsedTime, now = performance.now();
    if (flyTo) {
      const k = Math.min(1, (now - flyTo.start) / 900), e = ease(k);
      camera.position.lerpVectors(flyTo.fromPos, flyTo.pos, e); controls.target.lerpVectors(flyTo.fromTarget, flyTo.target, e);
      if (k >= 1) flyTo = null;
    }
    controls.update();
    for (const [id, n] of Object.entries(nodes)) {
      const g = n.group, u = g.userData;
      g.position.lerp(n.base, Math.min(1, dt * 4));            // glide when the layout changes
      if (n.spawnAt) {
        const k = Math.min(1, (now - n.spawnAt) / 800); g.scale.setScalar(Math.max(.001, easeBack(k)));
        if (k >= 1) n.spawnAt = null;
      }
      const busy = n.kind === "jarvis" || ACTIVE.includes(n.status);
      if (n.aura) {   // fade the working aura in and out, and keep it turning while the agent works
        const working = n.status === "working" || n.status === "starting";
        n.auraLevel += ((working ? 1 : 0) - n.auraLevel) * Math.min(1, dt * 3);
        const a = n.aura, au = a.userData, lvl = n.auraLevel;
        a.visible = lvl > .02;
        if (a.visible) {
          au.ring.rotation.z -= dt * 2.2;
          const beat = .5 + .5 * Math.sin(t * 4 + id.length);
          au.ring.material.opacity = lvl * (.65 + .35 * beat);
          au.glow.material.opacity = lvl * (.12 + .14 * beat);
          au.sparks.forEach((sp, i) => {
            const ang = t * 1.8 + i * Math.PI * 2 / 3, h = au.height * (.25 + .2 * Math.sin(t * 2.4 + i * 2));
            sp.position.set(Math.cos(ang) * au.r, .2 + h, Math.sin(ang) * au.r);
            sp.material.opacity = lvl * (.7 + .3 * Math.sin(t * 6 + i));
          });
        }
      }
      for (const [obj, sx, sy, sz] of u.spin || []) { obj.rotation.x += (sx || 0) * dt * (busy ? 1 : .3); obj.rotation.y += (sy || 0) * dt * (busy ? 1 : .3); obj.rotation.z += (sz || 0) * dt * (busy ? 1 : .3); }
      if (u.float) u.float.position.y = (u.floatBase ??= u.float.position.y) + (busy ? Math.sin(t * 2 + id.length) * .15 : 0);
      if (u.pulse) { const k = Math.sin(t * (busy ? 3 : 1.2) + id.length); u.pulse.scale.set(1 + k * .07, 1 - k * .1, 1 + k * .07); }
      if (u.fire) u.fire.forEach((f, i) => {
        const k = (1 + Math.sin(t * 13 + i * 2.1 + id.length) * .12 + Math.sin(t * 7.3 + i) * .08) * (busy ? 1 : .55) * f.userData.size;
        f.scale.set(k, k * 1.3, 1);
      });
      if (u.flicker) u.flicker.opacity = .25 + Math.sin(t * 8 + id.length) * .1;
      if (u.flag) u.flag.rotation.y = Math.sin(t * 3) * .35;
      if (u.scan) u.scan.position.y = -1.5 + ((t * .55 + id.length * .37) % 1) * 2.1;
      if (u.blink) u.blink.forEach((m, i) => { m.opacity = !busy ? .3 : Math.sin(t * 7 + i * 2.3 + id.length) > -.2 ? 1 : .15; });
      const talk = n.talkAt ? Math.max(0, 1 - (now - n.talkAt) / 700) : 0;
      const target = (id === hovered ? 1.12 : 1) + talk * .2;
      if (!n.spawnAt) g.scale.lerp(new THREE.Vector3(target, target, target), Math.min(1, dt * 10));
    }
    if (themeKey === "village") {
      smokeTimer -= dt;
      if (smokeTimer <= 0) {
        smokeTimer = .35;
        for (const n of Object.values(nodes)) if (n.group.userData.chimney && ACTIVE.includes(n.status)) {
          const c = n.group.userData.chimney.getWorldPosition(new THREE.Vector3()); c.y += .35; fx.smoke(c);
        }
      }
      scene.traverse(o => {
        if (o.userData.cloud) { o.position.x += o.userData.cloud * dt; if (o.position.x > 45) o.position.x = -45; }
        if (o.userData.waves) {
          const p = o.geometry.attributes.position;
          for (let i = 0; i < p.count; i++) p.setZ(i, Math.sin(p.getX(i) * .35 + t * 1.2) * .12 + Math.cos(p.getY(i) * .3 + t) * .12);
          p.needsUpdate = true;
        }
      });
    } else {
      scene.traverse(o => { if (o.userData.drift) o.rotation.y += dt * .02; });
    }
    theme.tick?.(scene, t, dt, fx);
    for (const link of Object.values(links)) {   // a line to Jarvis flares while a message travels it
      if (link.userData.path || link.userData.flash === undefined) continue;
      const base = (link.userData.base ??= link.material.opacity), k = Math.max(0, 1 - (now - link.userData.flash) / 1500);
      link.material.opacity = base + (1 - base) * Math.min(1, k * 1.5);
    }
    for (const [key, th] of Object.entries(threads)) {
      const A = nodes[th.a], B = nodes[th.b], age = now - th.last;
      if (!A || !B || age > 120000) { scene.remove(th.line); th.line.geometry.dispose(); delete threads[key]; continue; }
      th.line.geometry.setFromPoints(arcBetween(A, B).getPoints(28));
      th.line.material.opacity = .2 + .75 * Math.max(0, Math.min(1, 1 - age / 6000));
    }
    for (let i = effects.length - 1; i >= 0; i--) if (!effects[i](now)) effects.splice(i, 1);
    const pos = Object.fromEntries(Object.entries(nodes).map(([id, n]) => [id, n.group.position]));
    for (const id of Object.keys(links)) if (pos[id]) placeLink(id, pos.jarvis, pos[id]);
    composer ? composer.render() : renderer.render(scene, camera);
    labels.render(scene, camera);
    declutter(now);
    requestAnimationFrame(frame);
  }

  function resize() {
    const w = container.clientWidth || 800, h = container.clientHeight || 600;
    renderer.setSize(w, h); labels.setSize(w, h);
    camera.aspect = w / h; camera.updateProjectionMatrix();
    composer?.setSize(w, h);
    // back the starting view off in narrow windows so the whole team still fits
    const [pos, target] = homeBase, far = Math.min(1.8, Math.max(1, 1.75 / camera.aspect)) * fit;
    HOME.target.set(...target);
    HOME.pos.set(...pos).sub(HOME.target).multiplyScalar(far).add(HOME.target);
    if (camera.aspect < 1) HOME.pos.y += (1 - camera.aspect) * 14;   // tall, narrow screens: look down more, so rows spread out
  }
  new ResizeObserver(resize).observe(container);

  build(hooks.theme());
  requestAnimationFrame(frame);

  return {
    setTheme(key, agents) { build(key); sync(agents); },
    sync, message, focus, reset, themes: Object.keys(THEMES),
    toggleRotate() { controls.autoRotate = !controls.autoRotate; return controls.autoRotate; },
    stopRotate() { controls.autoRotate = false; },
    screenPosition(id) {   // where a node is on screen (page coordinates)
      const n = nodes[id]; if (!n) return null;
      const p = n.group.position.clone().setY(n.group.userData.height * .45).project(camera);
      const r = renderer.domElement.getBoundingClientRect();
      return { x: r.left + (p.x + 1) / 2 * r.width, y: r.top + (1 - p.y) / 2 * r.height };
    },
    canvas: renderer.domElement, labelsLayer: labels.domElement,
  };
}

function escapeHtml(s) { return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
