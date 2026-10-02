/**
 * beebox3d.js - beeBox 履约盒子的 WebGL 3D 渲染（Three.js）· 工厂流水线
 *
 * beeBox = 长方体设备箱（玻璃罩 + 金属框架）
 * 内部 = 传送导轨流水线：机器工位（机身 + 滚轮 + 状态指示灯）+
 * 隧道式 IN/OUT 端口 + 琥珀色包裹令牌沿导轨流动。
 *
 * 页面对外接口：window.BeeBox3D.init(container, ops) → scene api
 *
 * api:
 *   setOpState(opId, state)  state: '' | 'active' | 'done' | 'failed'
 *   moveToken(key)           key = opId | '_in' | '_out' | null（流出 OUT 口并隐藏）
 *   reset()                  清空工位状态 + 隐藏令牌
 */

import * as THREE from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

// 盒体尺寸：长方体（±x 壁 = 端口所在）
const BOX_W = 4.6, BOX_H = 3.0, BOX_D = 2.2;
const WALL_X = BOX_W / 2; // 2.3

// 指示灯颜色（机身不变，状态只体现在灯上——像真实设备的 status light）
const STATE_STYLE = {
  '':       { color: 0x94a3b8, emissive: 0x64748b, intensity: 0.25 },
  'active': { color: 0x60a5fa, emissive: 0x2563eb, intensity: 2.4 },
  'done':   { color: 0x4ade80, emissive: 0x16a34a, intensity: 1.4 },
  'failed': { color: 0xf87171, emissive: 0xdc2626, intensity: 2.4 },
};

function layoutOps(ops) {
  // 蛇形两排：row1 左→右（y=0.55），row2 右→左（y=-0.55）
  const pos = {};
  const n = ops.length;
  const perRow = Math.ceil(n / 2);
  const xs = (c) => {
    if (c === 1) return [0];
    return Array.from({ length: c }, (_, i) => -1.7 + (3.4 / (c - 1)) * i);
  };
  const xs1 = xs(perRow);
  for (let i = 0; i < perRow; i++) pos[ops[i]] = new THREE.Vector3(xs1[i], 0.55, 0);
  const rest = ops.slice(perRow);
  const xs2 = xs(rest.length);
  for (let i = 0; i < rest.length; i++) pos[rest[i]] = new THREE.Vector3(xs2[rest.length - 1 - i], -0.55, 0);
  pos._in = new THREE.Vector3(-WALL_X + 0.25, 0, 0);
  pos._out = new THREE.Vector3(WALL_X - 0.25, 0, 0);
  return pos;
}

function makeLabel(text) {
  const c = document.createElement('canvas');
  c.width = 512; c.height = 96;
  const ctx = c.getContext('2d');
  ctx.font = '600 40px -apple-system, BlinkMacSystemFont, sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  // 半透明底板，透过玻璃也能读
  ctx.fillStyle = 'rgba(248,250,252,0.88)';
  const w = Math.min(496, ctx.measureText(text).width + 56);
  ctx.beginPath();
  ctx.roundRect((512 - w) / 2, 14, w, 68, 14);
  ctx.fill();
  ctx.fillStyle = '#1e293b';
  ctx.fillText(text, 256, 50);
  const tex = new THREE.CanvasTexture(c);
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }));
  sp.scale.set(1.05, 0.2, 1);
  return sp;
}

function makeShadowTexture() {
  const c = document.createElement('canvas');
  c.width = 256; c.height = 256;
  const ctx = c.getContext('2d');
  const g = ctx.createRadialGradient(128, 128, 10, 128, 128, 128);
  g.addColorStop(0, 'rgba(15,23,42,0.30)');
  g.addColorStop(1, 'rgba(15,23,42,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 256, 256);
  return new THREE.CanvasTexture(c);
}

class BeeBoxScene {
  constructor(container, ops) {
    this.container = container;
    this.ops = ops;
    this.opState = {};
    this.stations = {};
    this.tokenTarget = null;
    this._flyingOff = false;

    const w = container.clientWidth || 560;
    const h = container.clientHeight || 420;

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setSize(w, h);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.1;
    container.appendChild(this.renderer.domElement);

    this.scene = new THREE.Scene();
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(this.renderer), 0.04).texture;

    this.camera = new THREE.PerspectiveCamera(38, w / h, 0.1, 100);
    this.camera.position.set(4.9, 2.9, 7.0);
    this.camera.lookAt(0, -0.1, 0);

    // 光照
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.45));
    const dir = new THREE.DirectionalLight(0xffffff, 1.6);
    dir.position.set(4, 6, 5);
    this.scene.add(dir);
    const fill = new THREE.DirectionalLight(0xc4b5fd, 0.4);
    fill.position.set(-5, 2, -3);
    this.scene.add(fill);

    // 盒子组（悬浮 + 鼠标倾斜都作用在它上面）
    this.group = new THREE.Group();
    this.scene.add(this.group);
    this.tiltTarget = { x: 0, y: 0 };

    this._buildBox();
    this._buildPorts();
    this._buildBelt();
    this._buildStations();
    this._buildToken();
    this._buildGroundShadow();

    container.addEventListener('mousemove', (e) => {
      const r = container.getBoundingClientRect();
      this.tiltTarget.x = ((e.clientY - r.top) / r.height - 0.5) * -0.18;
      this.tiltTarget.y = ((e.clientX - r.left) / r.width - 0.5) * 0.35;
    });
    container.addEventListener('mouseleave', () => {
      this.tiltTarget.x = 0;
      this.tiltTarget.y = 0;
    });

    this.clock = new THREE.Clock();
    this._animate();
  }

  _buildBox() {
    // 长方体玻璃罩（薄壁清透）
    const geo = new THREE.BoxGeometry(BOX_W, BOX_H, BOX_D);
    const glass = new THREE.MeshPhysicalMaterial({
      transmission: 1.0,
      thickness: 0.4,
      roughness: 0.05,
      ior: 1.45,
      color: 0xffffff,
      attenuationColor: 0xe2e8f0,
      attenuationDistance: 14,
      clearcoat: 0.4,
      clearcoatRoughness: 0.2,
      envMapIntensity: 1.0,
      specularIntensity: 0.9,
      transparent: true,
    });
    this.group.add(new THREE.Mesh(geo, glass));

    // 金属框架：12 根棱柱（设备骨架）+ 8 个角件铆接球头
    const frameMat = new THREE.MeshStandardMaterial({ color: 0x64748b, metalness: 0.85, roughness: 0.3 });
    const edges = new THREE.EdgesGeometry(geo);
    const pts = edges.attributes.position;
    for (let i = 0; i < pts.count; i += 2) {
      const a = new THREE.Vector3().fromBufferAttribute(pts, i);
      const b = new THREE.Vector3().fromBufferAttribute(pts, i + 1);
      this.group.add(this._barBetween(a, b, 0.035, frameMat));
    }
    const cornerGeo = new THREE.SphereGeometry(0.06, 14, 14);
    for (const sx of [-1, 1]) for (const sy of [-1, 1]) for (const sz of [-1, 1]) {
      const corner = new THREE.Mesh(cornerGeo, frameMat);
      corner.position.set(sx * BOX_W / 2, sy * BOX_H / 2, sz * BOX_D / 2);
      this.group.add(corner);
    }
  }

  _barBetween(a, b, radius, material) {
    const len = a.distanceTo(b);
    const bar = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, len, 10), material);
    bar.position.copy(a).lerp(b, 0.5);
    bar.quaternion.setFromUnitVectors(
      new THREE.Vector3(0, 1, 0),
      b.clone().sub(a).normalize()
    );
    return bar;
  }

  _buildPorts() {
    // IN：左壁琥珀色发光环 + 隧道筒 + 深色洞口
    const tunnelMat = new THREE.MeshStandardMaterial({ color: 0x334155, metalness: 0.7, roughness: 0.5, side: THREE.DoubleSide });

    const inRing = new THREE.Mesh(
      new THREE.TorusGeometry(0.3, 0.055, 16, 48),
      new THREE.MeshStandardMaterial({ color: 0xf59e0b, emissive: 0xd97706, emissiveIntensity: 0.9, roughness: 0.3, metalness: 0.5 })
    );
    inRing.position.set(-WALL_X - 0.01, 0, 0);
    inRing.rotation.y = Math.PI / 2;
    this.group.add(inRing);
    const inHole = new THREE.Mesh(
      new THREE.CircleGeometry(0.29, 32),
      new THREE.MeshBasicMaterial({ color: 0x0f172a })
    );
    inHole.position.set(-WALL_X + 0.005, 0, 0);
    inHole.rotation.y = -Math.PI / 2;
    this.group.add(inHole);
    const inTunnel = new THREE.Mesh(new THREE.CylinderGeometry(0.29, 0.29, 0.3, 32, 1, true), tunnelMat);
    inTunnel.position.set(-WALL_X + 0.14, 0, 0);
    inTunnel.rotation.z = Math.PI / 2;
    this.group.add(inTunnel);

    // OUT：右壁青色发光环 + 隧道筒 + 深色洞口
    const outRing = new THREE.Mesh(
      new THREE.TorusGeometry(0.34, 0.06, 16, 48),
      new THREE.MeshStandardMaterial({ color: 0x14b8a6, emissive: 0x0d9488, emissiveIntensity: 0.9, roughness: 0.3, metalness: 0.5 })
    );
    outRing.position.set(WALL_X + 0.01, 0, 0);
    outRing.rotation.y = Math.PI / 2;
    this.group.add(outRing);
    const outHole = new THREE.Mesh(
      new THREE.CircleGeometry(0.33, 32),
      new THREE.MeshBasicMaterial({ color: 0x0f172a })
    );
    outHole.position.set(WALL_X - 0.005, 0, 0);
    outHole.rotation.y = Math.PI / 2;
    this.group.add(outHole);
    const outTunnel = new THREE.Mesh(new THREE.CylinderGeometry(0.33, 0.33, 0.3, 32, 1, true), tunnelMat);
    outTunnel.position.set(WALL_X - 0.14, 0, 0);
    outTunnel.rotation.z = Math.PI / 2;
    this.group.add(outTunnel);
  }

  _beltPath(pos) {
    // 导轨完整路径：IN 口 → 各工位（蛇形）→ 盒底 → OUT 口
    const last = pos[this.ops[this.ops.length - 1]];
    return [
      new THREE.Vector3(-WALL_X + 0.1, 0, 0),
      pos._in,
      ...this.ops.map((op) => pos[op]),
      new THREE.Vector3(last.x, -1.15, 0),
      new THREE.Vector3(pos._out.x, -1.15, 0),
      pos._out,
      new THREE.Vector3(WALL_X - 0.1, 0, 0),
    ];
  }

  _buildBelt() {
    const pos = layoutOps(this.ops);
    this.positions = pos;
    // 导轨：一根贯穿全线的金属管，一眼看清流向
    const curve = new THREE.CatmullRomCurve3(this._beltPath(pos));
    const tube = new THREE.Mesh(
      new THREE.TubeGeometry(curve, 96, 0.04, 12, false),
      new THREE.MeshStandardMaterial({ color: 0x475569, metalness: 0.75, roughness: 0.35 })
    );
    this.group.add(tube);
  }

  _buildStations() {
    const pos = this.positions;

    for (const op of this.ops) {
      const p = pos[op];
      const g = new THREE.Group();
      g.position.copy(p);

      // 滚轮（导轨下的托辊，工厂感）
      const roller = new THREE.Mesh(
        new THREE.CylinderGeometry(0.07, 0.07, 0.42, 16),
        new THREE.MeshStandardMaterial({ color: 0x94a3b8, metalness: 0.8, roughness: 0.3 })
      );
      roller.rotation.x = Math.PI / 2;
      roller.position.y = -0.1;
      g.add(roller);
      // 机身：骑跨在导轨上的深色机器块
      const body = new THREE.Mesh(
        new THREE.BoxGeometry(0.34, 0.22, 0.34),
        new THREE.MeshStandardMaterial({ color: 0x334155, metalness: 0.6, roughness: 0.45 })
      );
      g.add(body);
      // 机身顶盖（浅色盖板，增加层次）
      const cap = new THREE.Mesh(
        new THREE.BoxGeometry(0.36, 0.04, 0.36),
        new THREE.MeshStandardMaterial({ color: 0x94a3b8, metalness: 0.7, roughness: 0.35 })
      );
      cap.position.y = 0.13;
      g.add(cap);
      // 状态指示灯（设备顶部的小灯——状态全部体现在这里）
      const lamp = new THREE.Mesh(
        new THREE.SphereGeometry(0.07, 20, 20),
        new THREE.MeshStandardMaterial({ color: 0x94a3b8, emissive: 0x64748b, emissiveIntensity: 0.25, roughness: 0.3 })
      );
      lamp.position.y = 0.23;
      g.add(lamp);

      const label = makeLabel(op.length > 16 ? op.slice(0, 15) + '…' : op);
      label.position.set(0, -0.34, 0);
      g.add(label);

      this.group.add(g);
      this.stations[op] = { group: g, lamp, state: '' };
      this.opState[op] = '';
    }
  }

  _buildToken() {
    // 包裹令牌：琥珀色发光小箱子，沿导轨流动
    this.token = new THREE.Group();
    const pack = new THREE.Mesh(
      new THREE.BoxGeometry(0.18, 0.14, 0.18),
      new THREE.MeshStandardMaterial({ color: 0xf59e0b, emissive: 0xd97706, emissiveIntensity: 0.9, roughness: 0.4 })
    );
    this.token.add(pack);
    this.tokenLight = new THREE.PointLight(0xfbbf24, 2.0, 2.2);
    this.token.add(this.tokenLight);
    this.token.visible = false;
    this.group.add(this.token);
  }

  _buildGroundShadow() {
    const plane = new THREE.Mesh(
      new THREE.PlaneGeometry(7.5, 4.6),
      new THREE.MeshBasicMaterial({ map: makeShadowTexture(), transparent: true, depthWrite: false })
    );
    plane.rotation.x = -Math.PI / 2;
    plane.position.y = -1.85;
    this.scene.add(plane);
  }

  setOpState(opId, state) {
    const st = this.stations[opId];
    if (!st) return;
    st.state = state;
    this.opState[opId] = state;
    const style = STATE_STYLE[state] || STATE_STYLE[''];
    st.lamp.material.color.setHex(style.color);
    st.lamp.material.emissive.setHex(style.emissive);
    st.lamp.material.emissiveIntensity = style.intensity;
  }

  moveToken(key) {
    if (key === null) {
      // 完成：流出 OUT 口后隐藏
      if (this.token.visible) {
        this._flyingOff = true;
        this.tokenTarget = new THREE.Vector3(WALL_X + 0.9, 0, 0);
      }
      return;
    }
    const p = this.positions[key];
    if (!p) return;
    this._flyingOff = false;
    // 包裹骑在导轨上方
    const target = p.clone().add(new THREE.Vector3(0, 0.15, 0));
    if (!this.token.visible) {
      this.token.position.copy(target);
      this.token.visible = true;
    }
    this.tokenTarget = target;
  }

  reset() {
    for (const op of this.ops) this.setOpState(op, '');
    this.tokenTarget = null;
    this._flyingOff = false;
    this.token.visible = false;
  }

  _animate() {
    requestAnimationFrame(() => this._animate());
    const t = this.clock.getElapsedTime();

    // 悬浮呼吸 + 鼠标倾斜（缓动）
    this.group.position.y = Math.sin(t * 1.2) * 0.06;
    this.group.rotation.x += (this.tiltTarget.x - this.group.rotation.x) * 0.06;
    this.group.rotation.y += (this.tiltTarget.y - this.group.rotation.y) * 0.06;

    // 包裹沿导轨流动
    if (this.token.visible && this.tokenTarget) {
      this.token.position.lerp(this.tokenTarget, 0.12);
      if (this._flyingOff && this.token.position.distanceTo(this.tokenTarget) < 0.15) {
        this.token.visible = false;
        this._flyingOff = false;
        this.tokenTarget = null;
      }
    }

    // active 工位指示灯脉冲呼吸
    for (const op of this.ops) {
      const st = this.stations[op];
      const base = (STATE_STYLE[st.state] || STATE_STYLE['']).intensity;
      const target = st.state === 'active' ? base * (0.7 + 0.5 * Math.sin(t * 6)) : base;
      st.lamp.material.emissiveIntensity += (target - st.lamp.material.emissiveIntensity) * 0.25;
    }

    this.renderer.render(this.scene, this.camera);
  }
}

window.BeeBox3D = {
  init(container, ops) {
    try {
      return new BeeBoxScene(container, ops);
    } catch (e) {
      console.error('BeeBox3D init failed:', e);
      container.innerHTML = '<div style="padding:40px;text-align:center;color:#94a3b8;">WebGL 不可用，无法渲染 3D 盒子</div>';
      return null;
    }
  },
};
window._bee3dReady = true;
window.dispatchEvent(new Event('beebox3d-ready'));
