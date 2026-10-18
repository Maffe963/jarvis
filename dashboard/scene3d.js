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
const sprite = (tex, size, opts = {}) => {
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, ...opts }));
  s.scale.set(size, size, 1); return s;
};
const mat = (color, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness: .8, metalness: .05, flatShading: true, ...extra });

// ---------- themes ----------
const THEMES = {
  command: {
    background: 0x070b12, bloom: [0.55, 0.35, 0.4],
    status: { starting: 0x38d6ff, working: 0x38d6ff, waiting: 0xffb547, "needs you": 0xff5c6c, done: 0x4ee49a, stopped: 0x5b6577, failed: 0xff5c6c },
    world(scene) {
      scene.fog = new THREE.FogExp2(0x070b12, 0.035);
      scene.add(new THREE.AmbientLight(0x6688aa, 0.6));
      const key = new THREE.PointLight(0x38d6ff, 60, 40); key.position.set(0, 8, 4); scene.add(key);
      const grid = new THREE.GridHelper(80, 80, 0x1d6f8f, 0x0f2a3c); grid.position.y = -1.6; scene.add(grid);
      const stars = new THREE.BufferGeometry(), pts = [];
      for (let i = 0; i < 900; i++) pts.push((Math.random() - .5) * 120, Math.random() * 50 + 2, (Math.random() - .5) * 120);
      stars.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
      scene.add(new THREE.Points(stars, new THREE.PointsMaterial({ color: 0x6fbfe0, size: .12 })));
    },
    node(kind, color) {
      const g = new THREE.Group(), size = kind === "jarvis" ? 1.3 : kind === "user" ? .75 : .85;
      const geo = kind === "jarvis" ? new THREE.IcosahedronGeometry(size, 1) : kind === "user" ? new THREE.DodecahedronGeometry(size) : new THREE.OctahedronGeometry(size);
      const core = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: .8, metalness: .3, roughness: .3, flatShading: true }));
      const shell = new THREE.Mesh(new THREE.IcosahedronGeometry(size * 1.45, 1), new THREE.MeshBasicMaterial({ color, wireframe: true, transparent: true, opacity: .22 }));
      g.add(core, shell);
      g.userData = { tint: [core.material, shell.material], spin: [[shell, .4, .25]], float: core, height: size * 1.6 };
      return g;
    },
    link: color => new THREE.LineBasicMaterial({ color, transparent: true, opacity: .45 }),
    particle(color) {
      const s = sprite(glowTexture(), .9, { color, blending: THREE.AdditiveBlending });
      return s;
    },
    burst(scene, at, fx) { fx.ring(at, 0x4ee49a, 3); },
  },

  hud: {
    background: 0x01050a, bloom: [0.9, 0.5, 0.2],
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
    background: 0x8fd6ff, bloom: null, shadows: true, home: [[0, 12.5, 17], [0, -1, 1.2]],
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
    const y = themeKey === "hud" ? -.2 : 0;
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
      scene.traverse(o => { o.geometry?.dispose(); (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => m?.dispose()); });
    }
    for (const k of Object.keys(nodes)) delete nodes[k];
    for (const k of Object.keys(links)) delete links[k];
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

  function message(from, to, kind) {
    const a = nodes[from], b = nodes[to]; if (!a || !b) return;
    const color = kind === "result" ? theme.status.done : to === "user" ? theme.status["needs you"] : kind === "user" ? theme.status.waiting : 0xa98bff;
    const p = theme.particle(color, kind); scene.add(p);
    const trail = themeKey === "village" ? [] : Array.from({ length: 10 }, (_, i) => {
      const s = sprite(glowTexture(), .5 - i * .035, { color, blending: THREE.AdditiveBlending, opacity: .5 - i * .045 }); scene.add(s); return s;
    });
    const start = performance.now(), dur = themeKey === "village" ? 1700 : 1200;
    const A = a.group.position.clone().setY(a.group.userData.height * .55), B = b.group.position.clone().setY(b.group.userData.height * .55);
    const mid = A.clone().add(B).multiplyScalar(.5); mid.y += A.distanceTo(B) * (themeKey === "village" ? .45 : .3);
    const curve = new THREE.QuadraticBezierCurve3(A, mid, B), history = [];
    effects.push(now => {
      const k = Math.min(1, (now - start) / dur), at = curve.getPoint(ease(k));
      p.position.copy(at); history.unshift(at.clone()); history.length = Math.min(history.length, 30);
      trail.forEach((s, i) => s.position.copy(history[Math.min(history.length - 1, i * 3)]));
      if (k >= 1) {
        scene.remove(p); trail.forEach(s => scene.remove(s));
        if (kind === "result") theme.burst(scene, B.clone(), fx); else fx.ring(B.clone().setY(-1.3), color, 1.2);
        return false;
      }
      return true;
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
      const target = id === hovered ? 1.12 : 1;
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
    sync, message, focus, reset,
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
