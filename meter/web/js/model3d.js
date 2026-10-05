/* The Collection's 3D viewer: a collectible's model, turned with the mouse.

   Raw WebGL rather than a 3D library: three.js alone would be half the
   page's size, and the page must stay under WebView2's 2 MB. One canvas for
   the page's lifetime, moved from panel to panel as the Collection rebuilds
   (a canvas keeps its WebGL context when moved).

   The meter builds a model on demand (notify 'coll_model') and hands it back
   through window.addModel(id, json): positions and UVs as float32, normals
   as int8, each material's triangles and its palette (the game's gradients,
   already composed) as a PNG. Shaded in two tones, like the game. */

const M3D = {
  models: {},          // id -> parsed payload, or null (no model)
  asked: {},           // id -> true once asked
  cur: null,           // the id shown
  gl: null, canvas: null, prog: null, loc: null,
  mesh: null,          // GPU buffers of the shown model
  yaw: 0.65, pitch: 0.18, dist: 1, dist0: 1, lift: 0, center: [0, 0, 0], radius: 1,
  spin: true, drag: null, raf: 0, onState: null,
};

function m3dSupported() {
  if (M3D.gl) return true;
  try {
    const c = document.createElement('canvas');
    return !!(c.getContext('webgl2') || c.getContext('webgl'));
  } catch (e) { return false; }
}

function m3dB64(s, Type) {
  const bin = atob(s);
  const buf = new ArrayBuffer(bin.length);
  const u8 = new Uint8Array(buf);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  return new Type(buf);
}

window.addModel = function (id, json) {
  let m = null;
  try { m = json ? JSON.parse(json) : null; } catch (e) { m = null; }
  M3D.models[id] = m;
  if (id === M3D.cur) m3dLoad(id);
};

/* The canvas, for a panel to hold. `onState(state)` hears 'loading',
   'ready' or 'none' (no model: the panel shows the picture instead).
   `opts.pitch`: how far above it the camera starts (gliders: seen from above).
   `opts.anim`: with its idle animation (a monster), where WebGL2 can play it.
   `opts.dist`: how far back the camera starts (1: the model fills the view);
   `opts.lift`: how much higher than the middle the model stands, in radii
   (room under it for a caption). `opts.spin`: false to stand still,
   `opts.yaw` facing that way. */
function m3dCanvas(id, onState, opts) {
  if (!M3D.canvas) m3dInit();
  M3D.onState = onState;
  M3D.pitch0 = (opts && opts.pitch) || 0.18;
  M3D.dist0 = (opts && opts.dist) || 1;
  M3D.lift = (opts && opts.lift) || 0;
  M3D.spin0 = !(opts && opts.spin === false);
  M3D.yaw0 = (opts && opts.yaw) || 0.65;
  if (opts && opts.anim && M3D.gl2) id += '@anim';
  if (id !== M3D.cur) {
    M3D.cur = id;
    M3D.spin = M3D.spin0;
    M3D.yaw = M3D.yaw0;
    M3D.pitch = M3D.pitch0;
    if (id in M3D.models) m3dLoad(id);
    else {
      m3dFree();
      onState('loading');
      if (!M3D.asked[id]) { M3D.asked[id] = true; notify('coll_model', { id }); }
    }
  } else {
    onState(M3D.mesh ? 'ready' : (id in M3D.models) ? 'none' : 'loading');
  }
  m3dLoop();
  return M3D.canvas;
}

function m3dInit() {
  const c = document.createElement('canvas');
  c.className = 'm3d';
  const opts = { antialias: true, alpha: true, premultipliedAlpha: true };
  let gl = c.getContext('webgl2', opts);
  if (!gl) {
    gl = c.getContext('webgl', opts);
    if (gl) gl.getExtension('OES_element_index_uint');
  }
  M3D.canvas = c;
  M3D.gl = gl;
  // animation needs float textures read from the vertex shader: WebGL2
  M3D.gl2 = !!gl && typeof WebGL2RenderingContext !== 'undefined'
    && gl instanceof WebGL2RenderingContext;
  if (!gl) return;
  // prefab.GradMatShader's fragment, as the game's HXSL has it, lit by one
  // light that follows the camera and without cast shadows
  // skinning: each joint's matrix in a frame is three texels (its columns)
  // of a float texture, a row per frame; between two frames, a blend
  const vs = `attribute vec3 aP; attribute vec3 aN; attribute vec2 aU; attribute vec2 aU2;
    attribute vec4 aJ; attribute vec4 aW;
    uniform mat4 uMVP; uniform float uAnim; uniform sampler2D uPal;
    uniform vec2 uPalSize; uniform vec3 uF;
    varying vec3 vN; varying vec2 vU; varying vec2 vU2;
    vec4 col(float j, float c, float f) {
      return texture2D(uPal, vec2((j * 3.0 + c + 0.5) / uPalSize.x, (f + 0.5) / uPalSize.y));
    }
    vec4 colAt(float j, float c) { return mix(col(j, c, uF.x), col(j, c, uF.y), uF.z); }
    void main() {
      vec3 p = aP, n = aN;
      if (uAnim > 0.5) {
        vec4 P = vec4(aP, 1.0), N = vec4(aN, 0.0);
        vec3 sp = vec3(0.0), sn = vec3(0.0);
        float tw = 0.0;
        for (int k = 0; k < 4; k++) {
          float w = aW[k];
          if (w > 0.0) {
            vec4 c0 = colAt(aJ[k], 0.0), c1 = colAt(aJ[k], 1.0), c2 = colAt(aJ[k], 2.0);
            sp += w * vec3(dot(P, c0), dot(P, c1), dot(P, c2));
            sn += w * vec3(dot(N, c0), dot(N, c1), dot(N, c2));
            tw += w;
          }
        }
        if (tw > 0.0) { p = sp / tw; n = sn; }
      }
      vN = n; vU = aU; vU2 = aU2; gl_Position = uMVP * vec4(p, 1.0);
    }`;
  const fs = `precision highp float;
    uniform sampler2D uG; uniform sampler2D uLines;
    uniform float uSlots; uniform float uMax; uniform float uOffs[16];
    uniform sampler2D uPat; uniform sampler2D uAlpha; uniform vec2 uUse;  // pattern, alpha pattern
    uniform vec3 uL; uniform vec3 uV; uniform vec3 uCX; uniform vec3 uCY;
    uniform vec4 uA;   // shadowBias, shadowSmooth, lightSmooth, terminatorSize
    uniform vec4 uB;   // specSize, specSmooth, specAlpha, linesBlend
    uniform vec4 uC;   // rimLightAngle (rad), rimLightWidth, rimLightSize, rimLightSmooth
    uniform vec4 uD;   // rimLightSpecMultiplier, emissivePow, specInsideSize, specInsideIntensity
    uniform vec4 uGlass;  // see-through glass: on, Fresnel bias, scale, power
    varying vec3 vN; varying vec2 vU; varying vec2 vU2;
    vec3 grad(float slot, float col, float v) {
      return texture2D(uG, vec2((slot * 6.0 + col + 0.5) / (uSlots * 6.0), v)).rgb;
    }
    vec3 overlay(vec3 base, vec3 over, float a) {
      return mix(base, mix(1.0 - 2.0 * (1.0 - base) * (1.0 - over), 2.0 * base * over,
        step(base, vec3(0.5))), a);
    }
    float ilerp(float x, float a, float b) { return clamp((x - a) / (b - a), 0.0, 1.0); }
    void main() {
      vec3 n = normalize(vN);
      if (!gl_FrontFacing) n = -n;
      if (uUse.y > 0.5 && vU2.x >= 0.49 && vU2.y >= 0.49
          && texture2D(uAlpha, (vU2 - 0.5) * 2.0).x < 0.5) discard;
      float slot = clamp(floor(vU.x * uMax), 0.0, uSlots - 1.0);
      float off = 0.0;
      for (int i = 0; i < 16; i++) if (float(i) == slot) off = uOffs[i];
      float v = vU.y + off;
      vec3 albedo = grad(slot, 0.0, v);
      vec3 shadowC = grad(slot, 1.0, v) * albedo;
      vec3 termC = grad(slot, 2.0, v);
      vec3 specC = grad(slot, 3.0, v);
      vec3 emis = grad(slot, 4.0, v) * uD.y;
      vec4 lines = texture2D(uLines, vU2);
      if (uUse.x > 0.5 && vU2.x >= 0.49 && vU2.y >= 0.49) lines = texture2D(uPat, (vU2 - 0.5) * 2.0);
      float ndl = dot(n, uL) - uA.x;
      float lighting = ilerp(ndl, -uA.y, uA.y);
      vec3 h = normalize(uL + uV);
      float ndh = clamp(dot(n, h), 0.0, 1.0);
      float spec = clamp((ndh - 1.0 + uB.x + uB.y) / (uB.y + 1e-5), 0.0, 1.0)
        + clamp((ndh - 1.0 + uB.x * uD.z + uB.y) / (uB.y + 1e-5), 0.0, 1.0) * uD.w;
      vec3 lit = clamp(albedo + specC * spec * uB.z + vec3(0.06) * lighting, 0.0, 1.0);
      vec3 c = mix(shadowC, lit, lighting);
      float ls = uA.z * uA.w;
      float term = min(lighting, 1.0 - ilerp(ndl, uA.w - ls, uA.w + ls));
      c = overlay(c, termC, term);
      vec2 cn = normalize(vec2(dot(n, uCX), dot(n, uCY)) + 1e-5);
      float dirn = clamp(dot(vec2(cos(uC.x), sin(uC.x)), cn) * uC.y, 0.0, 1.0);
      float fres = clamp(1.0 - pow(max(dot(uV, n), 0.0), 5.0), 0.0, 1.0);
      float rim = clamp((fres * dirn - 1.0 + uC.z + uC.w) / (uC.w + 1e-5), 0.0, 1.0);
      c = mix(c, albedo + specC * uD.x + vec3(0.15), rim);
      c = overlay(c, lines.rgb, uB.w * lines.a);
      c += emis;
      float alpha = 1.0;
      if (uGlass.x > 0.5) {
        alpha = clamp(uGlass.y + uGlass.z * pow(1.0 - abs(dot(uV, n)), max(uGlass.w, 0.01)), 0.0, 1.0);
      }
      gl_FragColor = vec4(clamp(c, 0.0, 1.0) * alpha, alpha);
    }`;
  const sh = (type, src) => {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    return s;
  };
  const p = gl.createProgram();
  gl.attachShader(p, sh(gl.VERTEX_SHADER, vs));
  gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(p);
  M3D.prog = p;
  M3D.loc = {};
  ['aP', 'aN', 'aU', 'aU2', 'aJ', 'aW'].forEach((k) => { M3D.loc[k] = gl.getAttribLocation(p, k); });
  ['uAnim', 'uPal', 'uPalSize', 'uF'].forEach((k) => { M3D.loc[k] = gl.getUniformLocation(p, k); });
  ['uMVP', 'uG', 'uLines', 'uSlots', 'uMax', 'uOffs', 'uPat', 'uAlpha', 'uUse', 'uL', 'uV', 'uCX', 'uCY', 'uA', 'uB', 'uC', 'uD', 'uGlass']
    .forEach((k) => { M3D.loc[k] = gl.getUniformLocation(p, k); });

  // turn it by dragging, closer and further with the wheel
  c.addEventListener('pointerdown', (e) => {
    M3D.drag = { x: e.clientX, y: e.clientY };
    M3D.spin = false;
    c.setPointerCapture(e.pointerId);
  });
  c.addEventListener('pointermove', (e) => {
    if (!M3D.drag) return;
    M3D.yaw -= (e.clientX - M3D.drag.x) * 0.01;
    M3D.pitch = Math.max(-0.4, Math.min(1.2, M3D.pitch + (e.clientY - M3D.drag.y) * 0.01));
    M3D.drag = { x: e.clientX, y: e.clientY };
  });
  const up = () => { M3D.drag = null; };
  c.addEventListener('pointerup', up);
  c.addEventListener('pointercancel', up);
  c.addEventListener('dblclick', () => {
    M3D.spin = M3D.spin0; M3D.yaw = M3D.yaw0; M3D.pitch = M3D.pitch0; M3D.dist = M3D.dist0;
  });
  c.addEventListener('wheel', (e) => {
    e.preventDefault();
    M3D.dist = Math.max(0.45, Math.min(2.2, M3D.dist * (e.deltaY > 0 ? 1.1 : 0.9)));
  }, { passive: false });
}

function m3dFree() {
  const gl = M3D.gl;
  if (gl && M3D.mesh) {
    const m = M3D.mesh;
    [m.pos, m.nor, m.uv, m.uv2, m.aj, m.aw].forEach((b) => b && gl.deleteBuffer(b));
    if (m.pal) gl.deleteTexture(m.pal);
    gl.deleteTexture(m.lines);
    m.parts.forEach((p) => {
      gl.deleteBuffer(p.ib);
      [p.tex, p.pat, p.alpha].forEach((t) => t && gl.deleteTexture(t));
    });
  }
  M3D.mesh = null;
}

function m3dLoad(id) {
  m3dFree();
  const d = M3D.models[id];
  const gl = M3D.gl;
  if (!d || !gl) { if (M3D.onState) M3D.onState('none'); return; }
  const pos = m3dB64(d.pos, Float32Array);
  const mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < pos.length; i += 3) {
    for (let k = 0; k < 3; k++) {
      if (pos[i + k] < mn[k]) mn[k] = pos[i + k];
      if (pos[i + k] > mx[k]) mx[k] = pos[i + k];
    }
  }
  // an animated model is framed on its pose, not on how it was modelled
  const box = d.anim && M3D.gl2 && d.anim.box;
  if (box) {
    for (let k = 0; k < 3; k++) { mn[k] = box[k]; mx[k] = box[k + 3]; }
  }
  M3D.center = [0, 1, 2].map((k) => (mn[k] + mx[k]) / 2);
  M3D.radius = Math.hypot(mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]) / 2 || 1;
  M3D.center[1] -= M3D.lift * M3D.radius;
  M3D.dist = M3D.dist0;
  const buf = (target, data) => {
    const b = gl.createBuffer();
    gl.bindBuffer(target, b);
    gl.bufferData(target, data, gl.STATIC_DRAW);
    return b;
  };
  // a texture from a PNG data URI; `done` once it is on the GPU
  const tex = (src, done) => {
    const t = gl.createTexture();
    const im = new Image();
    im.onload = () => {
      if (M3D.mesh !== mesh) return;
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, im);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      done();
    };
    im.onerror = done;
    if (src) im.src = src; else setTimeout(done);
    return t;
  };
  const loaded = () => {
    if (--mesh.pending === 0 && M3D.onState) M3D.onState('ready');
  };
  const mesh = {
    pos: buf(gl.ARRAY_BUFFER, pos),
    nor: buf(gl.ARRAY_BUFFER, m3dB64(d.nor, Int8Array)),
    uv: buf(gl.ARRAY_BUFFER, m3dB64(d.uv, Float32Array)),
    uv2: buf(gl.ARRAY_BUFFER, m3dB64(d.uv2, Float32Array)),
    parts: [], pending: d.parts.length + 1, lines: null,
  };
  M3D.mesh = mesh;
  mesh.lines = tex(d.lines, loaded);
  if (d.anim && M3D.gl2) {
    // the joints' matrices, frame by frame, in a float texture
    const a = d.anim;
    mesh.aj = buf(gl.ARRAY_BUFFER, m3dB64(a.j, Uint8Array));
    mesh.aw = buf(gl.ARRAY_BUFFER, m3dB64(a.w, Uint8Array));
    mesh.pal = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, mesh.pal);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, a.joints * 3, a.frames, 0, gl.RGBA, gl.FLOAT,
      m3dB64(a.pal, Float32Array));
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    mesh.anim = { fps: a.fps || 15, frames: a.frames, w: a.joints * 3, t0: performance.now() };
  }
  d.parts.forEach((p) => {
    const idx = m3dB64(p.idx, d.big ? Uint32Array : Uint16Array);
    const sh = p.shade || {};
    const offs = new Float32Array(16);
    (p.offs || []).forEach((o, i) => { if (i < 16) offs[i] = o; });
    const part = {
      ib: buf(gl.ELEMENT_ARRAY_BUFFER, idx), n: idx.length,
      type: d.big ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT,
      ok: false, slots: p.slots || 1, max: p.max || 8, offs,
      glass: p.glass ? [1, p.glass[0], p.glass[1], p.glass[2]] : null,
      cull: p.cull || 'Back', blend: p.blend || 'None',
      a: [sh.shadowBias, sh.shadowSmooth, sh.lightSmooth, sh.terminatorSize],
      b: [sh.specSize, sh.specSmooth, sh.specAlpha, sh.linesBlend],
      c: [(sh.rimLightAngle || 0) * Math.PI / 180, sh.rimLightWidth, sh.rimLightSize, sh.rimLightSmooth],
      d: [sh.rimLightSpecMultiplier, sh.emissivePow, sh.specInsideSize, sh.specInsideIntensity],
    };
    ['a', 'b', 'c', 'd'].forEach((k) => { part[k] = part[k].map((x) => Number(x) || 0); });
    // a step of 0.002 would alias into stairs on screen: a little wider
    part.a[1] = Math.max(part.a[1], 0.02);
    part.b[1] = Math.max(part.b[1], 0.01);
    part.tex = tex(p.grad, () => { part.ok = true; loaded(); });
    mesh.pending += 2;
    part.pat = p.pattern ? tex(p.pattern, loaded) : (setTimeout(loaded), null);
    part.alpha = p.alpha ? tex(p.alpha, loaded) : (setTimeout(loaded), null);
    mesh.parts.push(part);
  });
}

/* Column-major 4x4: perspective times look-at. */
function m3dMatrix(eye, at, aspect) {
  const f = 1 / Math.tan(0.6 / 2), near = M3D.radius * 0.05, far = M3D.radius * 20;
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const nrm = (a) => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const z = nrm(sub(eye, at)), x = nrm(cross([0, 1, 0], z)), y = cross(z, x);
  const v = [x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0,
    -dot(x, eye), -dot(y, eye), -dot(z, eye), 1];
  const p = [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) / (near - far), -1,
    0, 0, 2 * far * near / (near - far), 0];
  const o = new Array(16);
  for (let c = 0; c < 4; c++) {
    for (let r = 0; r < 4; r++) {
      let s = 0;
      for (let k = 0; k < 4; k++) s += p[k * 4 + r] * v[c * 4 + k];
      o[c * 4 + r] = s;
    }
  }
  return { m: new Float32Array(o), x, y, z };
}

function m3dLoop() {
  if (M3D.raf) return;
  let last = performance.now();
  const frame = (now) => {
    const c = M3D.canvas;
    // stop when the canvas has left the page; the next panel restarts it
    if (!c || !c.isConnected) { M3D.raf = 0; return; }
    M3D.raf = requestAnimationFrame(frame);
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    if (M3D.spin) M3D.yaw += dt * 0.35;
    m3dDraw();
  };
  M3D.raf = requestAnimationFrame(frame);
}

function m3dDraw() {
  const gl = M3D.gl, c = M3D.canvas;
  if (!gl) return;
  const zoom = parseFloat(document.documentElement.style.zoom) || 1;
  const dpr = (window.devicePixelRatio || 1) * zoom;
  const w = Math.max(1, Math.round(c.clientWidth * dpr));
  const h = Math.max(1, Math.round(c.clientHeight * dpr));
  if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  gl.viewport(0, 0, w, h);
  gl.clearColor(0, 0, 0, 0);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  const m = M3D.mesh;
  if (!m) return;
  const L = M3D.loc;
  // framed on the narrower of the two angles: the panel is often taller
  // than wide, and a lizard is long
  const half = Math.min(0.3, Math.atan(Math.tan(0.3) * w / h));
  const d = M3D.radius / Math.sin(half) * 0.92 * M3D.dist;
  const cp = Math.cos(M3D.pitch);
  const eye = [M3D.center[0] + d * Math.sin(M3D.yaw) * cp,
    M3D.center[1] + d * Math.sin(M3D.pitch),
    M3D.center[2] + d * Math.cos(M3D.yaw) * cp];
  const mat = m3dMatrix(eye, M3D.center, w / h);
  // the light follows the camera: from above, a little to its left
  const l = [0, 1, 2].map((k) => mat.z[k] * 0.7 + mat.y[k] * 0.9 - mat.x[k] * 0.45);
  const ll = Math.hypot(l[0], l[1], l[2]);
  gl.enable(gl.DEPTH_TEST);
  // as Heaps does: a glass shell lies exactly on the body it covers
  gl.depthFunc(gl.LEQUAL);
  gl.useProgram(M3D.prog);
  gl.uniformMatrix4fv(L.uMVP, false, mat.m);
  gl.uniform3f(L.uL, l[0] / ll, l[1] / ll, l[2] / ll);
  gl.uniform3f(L.uV, mat.z[0], mat.z[1], mat.z[2]);
  gl.uniform3f(L.uCX, mat.x[0], mat.x[1], mat.x[2]);
  gl.uniform3f(L.uCY, mat.y[0], mat.y[1], mat.y[2]);
  const attr = (loc, b, n, type, norm) => {
    if (loc < 0) return;
    gl.bindBuffer(gl.ARRAY_BUFFER, b);
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, n, type, norm, 0, 0);
  };
  attr(L.aP, m.pos, 3, gl.FLOAT, false);
  attr(L.aN, m.nor, 3, gl.BYTE, true);
  attr(L.aU, m.uv, 2, gl.FLOAT, false);
  attr(L.aU2, m.uv2, 2, gl.FLOAT, false);
  if (m.anim) {
    attr(L.aJ, m.aj, 4, gl.UNSIGNED_BYTE, false);
    attr(L.aW, m.aw, 4, gl.UNSIGNED_BYTE, true);
    const f = ((performance.now() - m.anim.t0) / 1000 * m.anim.fps) % m.anim.frames;
    const f0 = Math.floor(f);
    gl.uniform1f(L.uAnim, 1);
    gl.uniform2f(L.uPalSize, m.anim.w, m.anim.frames);
    gl.uniform3f(L.uF, f0, (f0 + 1) % m.anim.frames, f - f0);
    gl.activeTexture(gl.TEXTURE4);
    gl.bindTexture(gl.TEXTURE_2D, m.pal);
    gl.uniform1i(L.uPal, 4);
  } else {
    [L.aJ, L.aW].forEach((loc) => { if (loc >= 0) gl.disableVertexAttribArray(loc); });
    gl.uniform1f(L.uAnim, 0);
  }
  gl.activeTexture(gl.TEXTURE1);
  gl.bindTexture(gl.TEXTURE_2D, m.lines);
  gl.uniform1i(L.uLines, 1);
  gl.uniform1i(L.uPat, 2);
  gl.uniform1i(L.uAlpha, 3);
  gl.activeTexture(gl.TEXTURE0);
  gl.uniform1i(L.uG, 0);
  // the opaque parts, then the see-through glass over them
  const see = (p) => p.glass || p.blend !== 'None';
  const order = m.parts.filter((p) => !see(p)).concat(m.parts.filter(see));
  order.forEach((p) => {
    if (!p.ok) return;
    // each material's own faces: most show their front only, so a glass's
    // inner wall shows through its outer one
    if (p.cull === 'None') gl.disable(gl.CULL_FACE);
    else {
      gl.enable(gl.CULL_FACE);
      gl.cullFace(p.cull === 'Front' ? gl.FRONT : gl.BACK);
    }
    if (see(p)) {
      gl.enable(gl.BLEND);
      if (p.blend === 'Add') gl.blendFunc(gl.ONE, gl.ONE);
      else gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      gl.depthMask(false);
    }
    gl.uniform4fv(L.uGlass, p.glass || [0, 0, 0, 0]);
    gl.bindTexture(gl.TEXTURE_2D, p.tex);
    gl.uniform1f(L.uSlots, p.slots);
    gl.uniform1f(L.uMax, p.max);
    gl.uniform2f(L.uUse, p.pat ? 1 : 0, p.alpha ? 1 : 0);
    gl.activeTexture(gl.TEXTURE2);
    gl.bindTexture(gl.TEXTURE_2D, p.pat || m.lines);
    gl.activeTexture(gl.TEXTURE3);
    gl.bindTexture(gl.TEXTURE_2D, p.alpha || m.lines);
    gl.activeTexture(gl.TEXTURE0);
    gl.uniform1fv(L.uOffs, p.offs);
    gl.uniform4fv(L.uA, p.a);
    gl.uniform4fv(L.uB, p.b);
    gl.uniform4fv(L.uC, p.c);
    gl.uniform4fv(L.uD, p.d);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, p.ib);
    gl.drawElements(gl.TRIANGLES, p.n, p.type, 0);
    if (see(p)) {
      gl.disable(gl.BLEND);
      gl.depthMask(true);
    }
  });
}
