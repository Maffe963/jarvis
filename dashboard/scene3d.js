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
const sprite = (tex, size, opts = {}) => {
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, ...opts }));
  s.scale.set(size, size, 1); return s;
};
const mat = (color, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness: .8, metalness: .05, flatShading: true, ...extra });

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
    },
    tick(scene, t, dt) { scene.userData.sweep.rotation.z -= dt * .8; },
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
    },
    tick(scene, t) { scene.userData.grid.position.z = (t * 4) % 2; },   // the road rushing towards you
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
    },
    tick(scene, t, dt) { scene.userData.galaxy.rotation.y += dt * .03; },
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
    },
    tick(scene, t, dt) {
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
        const a = Math.random() * Math.PI * 2, d = 9 + Math.random() * 2.3;
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
        cloud.position.set((Math.random() - .5) * 70, 9 + Math.random() * 5, (Math.random() - .5) * 50 - 10);
        cloud.userData.cloud = .3 + Math.random() * .5; scene.add(cloud);
      }
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

  // ---- layout: Jarvis in the middle, the user behind, agents in an arc in front ----
  function layout() {
    const pos = { jarvis: new THREE.Vector3(0, 0, 0), user: new THREE.Vector3(0, 0, -6.5) };
    const ids = Object.keys(nodes).filter(id => nodes[id].kind === "agent");
    ids.forEach((id, i) => {
      const deg = ids.length === 1 ? 90 : -15 + (210 * i) / (ids.length - 1);
      const a = deg * Math.PI / 180;
      pos[id] = new THREE.Vector3(Math.cos(a) * 7, 0, Math.sin(a) * 6);
    });
    return pos;
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
    scene.add(group);
    nodes[id] = { group, pick, label, kind, status, info, spawnAt: kind === "agent" ? performance.now() : null, base: new THREE.Vector3() };
    if (kind === "agent") {
      const line = makeLink(id); links[id] = line; scene.add(line);
    }
    updateLabel(id);
    return nodes[id];
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
    n.label.element.innerHTML =
      `${icon ? `<div class="bubble">${icon}</div>` : ""}<div class="name">${badge ? `<span class="badge">${badge}</span>` : ""}${escapeHtml(name)}</div>` +
      (sub ? `<div class="state" style="--c:#${new THREE.Color(colorFor(n.status)).getHexString()}">${escapeHtml(sub)}</div>` : "") +
      (doing ? `<div class="doing">${escapeHtml(doing.length > 30 ? doing.slice(0, 29) + "…" : doing)}</div>` : "");
  }

  function recolor(id) {
    const n = nodes[id]; const c = new THREE.Color(colorFor(n.status));
    for (const m of n.group.userData.tint || []) {
      if (m.emissive) m.emissive.copy(c);
      m.color.copy(c);
    }
    const dim = n.status === "stopped" || n.status === "failed";
    n.group.traverse(o => {
      if (o.isMesh && o !== n.pick && o.material && !Array.isArray(o.material)) {
        o.material.transparent = true;
        o.material.opacity = dim ? .35 : (o.material.userData.baseOpacity ??= o.material.opacity);
      }
    });
    if (links[id] && links[id].material.color) links[id].material.color.copy(themeKey === "village" ? new THREE.Color(0xd9bd82) : c);
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
  renderer.domElement.addEventListener("pointerdown", ev => { downAt = [ev.clientX, ev.clientY]; controls.autoRotate = false; });
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
    const pos = layout();
    for (const [id, n] of Object.entries(nodes)) {
      n.base.copy(pos[id]);
      if (!n.placed) { n.group.position.copy(pos[id]); n.placed = true; if (n.spawnAt) fx.spawn(pos[id].clone(), colorFor(n.status)); }
    }
  }

  // ---- a message: a beam of light draws itself from sender to receiver, carrying a preview of what was said ----
  const threads = {};          // "a|b" -> { line, a, b, last }: a faint line left between agents who have been talking
  function arcBetween(a, b) {
    const A = a.group.position.clone().setY(a.group.userData.height * .55), B = b.group.position.clone().setY(b.group.userData.height * .55);
    const mid = A.clone().add(B).multiplyScalar(.5); mid.y += A.distanceTo(B) * (themeKey === "village" ? .45 : .3);
    return new THREE.QuadraticBezierCurve3(A, mid, B);
  }
  function message(from, to, kind, text) {
    const a = nodes[from], b = nodes[to]; if (!a || !b || from === to) return;
    const color = kind === "result" ? theme.status.done : to === "user" ? theme.status["needs you"] : kind === "user" ? theme.status.waiting : (theme.msg ?? 0xa98bff);
    const glowy = themeKey !== "village", start = performance.now(), dur = themeKey === "village" ? 1700 : 1200;
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
    let tag = null;   // what was said, riding along with it
    if (text) {
      const div = document.createElement("div"); div.className = "msg3d";
      div.textContent = text.length > 70 ? text.slice(0, 68) + "…" : text;
      div.style.setProperty("--c", "#" + new THREE.Color(color).getHexString());
      tag = new CSS2DObject(div); scene.add(tag);
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
        if (tag) tag.position.copy(at).setY(at.y + .7);
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
      if (tag) scene.remove(tag);
      return false;
    });
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
      for (const [obj, sx, sy, sz] of u.spin || []) { obj.rotation.x += (sx || 0) * dt * (busy ? 1 : .3); obj.rotation.y += (sy || 0) * dt * (busy ? 1 : .3); obj.rotation.z += (sz || 0) * dt * (busy ? 1 : .3); }
      if (u.float) u.float.position.y = busy ? Math.sin(t * 2 + id.length) * .15 : 0;
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
    theme.tick?.(scene, t, dt);
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
    requestAnimationFrame(frame);
  }

  function resize() {
    const w = container.clientWidth || 800, h = container.clientHeight || 600;
    renderer.setSize(w, h); labels.setSize(w, h);
    camera.aspect = w / h; camera.updateProjectionMatrix();
    composer?.setSize(w, h);
    // back the starting view off in narrow windows so the whole team still fits
    const [pos, target] = homeBase, far = Math.min(1.8, Math.max(1, 1.75 / camera.aspect));
    HOME.target.set(...target);
    HOME.pos.set(...pos).sub(HOME.target).multiplyScalar(far).add(HOME.target);
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
