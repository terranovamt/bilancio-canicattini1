/* mobile.js - versione VERTICALE (telefono). Va caricato dopo lo script principale. */
(function () {
    let PM = innerWidth < 820 && innerHeight > innerWidth, SH = 1280;
    const css = document.createElement('style');
    css.textContent = `
body.pm .hd{padding:0 24px}
body.pm #nav{left:14px;bottom:14px;gap:10px}
body.pm .nb{height:44px;min-width:44px;border-radius:22px;font-size:15px;opacity:.55}
body.pm .nb svg{width:22px;height:22px}
body.pm #cnt{font-size:20px;right:24px;bottom:20px}
body.pm .sec .l{width:100%;height:34%;bottom:auto}
body.pm .sec .l img{width:190px}
body.pm .sec .rr{left:0;top:34%;padding:0 40px}
body.pm .sec h1{font-size:56px}
body.pm .sec h2{font-size:24px}
body.pm .sec .rr:after{right:40px;top:40px}
body.pm .sec .d{right:40px;bottom:50px}
body.pm .gr{top:35%;height:auto;padding:40px;flex-direction:column;align-items:flex-start;gap:24px}
body.pm .gr h1{font-size:72px}body.pm .gr .bb{width:100%}body.pm .gr img{margin:0;height:100px}
body.pm .reg{left:24px;right:24px;top:96px;bottom:70px}
body.pm .mod .mt,body.pm .mod .nt2{left:30px;right:30px}
body.pm .mod .col{left:30px!important;width:660px!important}
body.pm .mod .col:not(.one){bottom:auto;height:calc((100% - 280px)/2)}
body.pm .mod .col[style*="left:655px"]{top:calc(158px + (100% - 280px)/2 + 20px)}
body.pm .row{left:30px!important;width:660px!important}
body.pm .row span{font-size:21px!important}
body.pm .row b{font-size:17px}
body.pm .p-cs>.card{left:30px!important;width:660px!important;top:96px!important}
body.pm .p-cs>svg{left:-590px;top:90px;transform:none!important}
body.pm .p-cs>.a{left:230px!important;top:435px!important;transform:none!important}
body.pm .p-cs .row{margin-top:380px}
body.pm .p-cm>.a{left:80px!important;top:96px!important}
body.pm .p-cm>.card{left:108px!important;top:670px!important}
body.pm .og{display:grid!important;grid-template-columns:110px 1fr;column-gap:20px;align-items:center;text-align:left!important;padding:14px 20px!important}
body.pm .og>div:nth-child(1){grid-column:2;justify-self:start}
body.pm .og>div:nth-child(2){grid-column:1;grid-row:1/4;width:96px!important;height:96px!important;margin:0!important;font-size:46px!important}
body.pm .og>div:nth-child(3),body.pm .og>div:nth-child(4){grid-column:2}`;
    document.head.append(css);

    /* palco: larghezza 720, altezza = quella dello schermo */
    fit = function () {
        const W = innerWidth, H = innerHeight; PM = W < 820 && H > W;
        document.body.classList.toggle('pm', PM);
        if (PM) { const k = W / 720; SH = H / k; st.style.width = '720px'; st.style.height = SH + 'px'; st.style.transform = `scale(${k})` }
        else { const k = Math.min(W / 1280, H / 720); st.style.width = '1280px'; st.style.height = '720px'; st.style.transform = `translate(${(W - 1280 * k) / 2}px,${(H - 720 * k) / 2}px) scale(${k})` }
    };
    let was = PM;
    addEventListener('resize', () => { fit(); if (PM !== was) { was = PM; if (window.DATI && scenes.length) start(window.DATI) } });
    fit();

    const E = (html) => h(html);
    const shrink = (r, k, top) => {
        const w = h(`<div style="position:absolute;left:${(720 - 1280 * k) / 2}px;top:${top}px;width:1280px;height:720px;transform:scale(${k});transform-origin:0 0"></div>`);
        [...r.children].forEach(c => w.append(c)); r.append(w);
    };

    /* ---- slide riscritte per il verticale ---- */
    const PORT = {
        slide_patrimonio_barre: () => (r, D) => {
            const P = D.patrimonio; band(r, 'STATO PATRIMONIALE DI GRUPPO'); cnt.className = '';
            const mx = Math.max(1, ...P.voci.flatMap(v => [v.importo_anno_precedente, v.importo_anno_corrente]));
            const stat = (x, a) => { const e = E(`<div class="card" style="left:${x}px;top:96px;width:320px;height:110px;padding:14px 22px"><div class="k" style="font-size:14px">${P.data_riferimento} ${a}</div><div class="num" style="font-size:34px"></div></div>`); r.append(e); return e.querySelector('.num') };
            const t1 = stat(30, P.anno_precedente), t2 = stat(370, P.anno_corrente);
            const hh = Math.min(150, (SH - 290) / P.voci.length);
            const els = P.voci.map((v, i) => {
                const bc = v.colore_css ? [`color-mix(in srgb,var(${v.colore_css}) 50%,transparent)`, `var(${v.colore_css})`] : v.colori_css;
                const br = (yr, val, c) => `<div style="display:flex;align-items:center;gap:10px;margin-top:7px"><span style="width:44px;font-size:12px;opacity:.6">${yr}</span><div style="flex:1"><div class="bw" data-w="${val / mx * 100}" style="height:16px;width:0;border-radius:8px;background:${c};transition:width 1s"></div></div><span style="width:120px;text-align:right;font-size:15px;font-weight:700">${val.toLocaleString('it-IT', { minimumFractionDigits: 2 })}</span></div>`;
                const e = E(`<div class="card fade" style="left:30px;top:${226 + i * hh}px;width:660px;height:${hh - 14}px;padding:12px 20px"><div style="display:flex;align-items:center;gap:12px"><img src="${v.icona}" style="height:34px"><b style="font-size:18px">${v.nome}</b></div>${br(P.anno_precedente, v.importo_anno_precedente, bc[0])}${br(P.anno_corrente, v.importo_anno_corrente, bc[1])}</div>`);
                r.append(e); return e
            });
            return { steps: P.voci.length, show(k) { let a = 0, b = 0; els.forEach((e, i) => { const on = i < k; e.classList.toggle('on', on); e.querySelectorAll('.bw').forEach(w => w.style.width = on ? w.dataset.w + '%' : '0'); if (on) { a += P.voci[i].importo_anno_precedente; b += P.voci[i].importo_anno_corrente } }); counter(t1, a); counter(t2, b) } }
        },
        slide_patrimonio_ciambella: () => (r, D) => {
            const P = D.patrimonio; band(r, 'STATO PATRIMONIALE DI GRUPPO'); cnt.className = '';
            const cols = [1, 2, 3, 4, 5, 6].map(i => V('--pie' + i)), ord = [5, 1, 4, 3, 2, 0].map(i => P.voci[i]);
            const T0 = sum(ord.map(x => x.importo_anno_corrente)) || 1, cx = 360, cy = 320, R = 140, W = 100, L = 2 * Math.PI * R;
            const svg = document.createElementNS(NS, 'svg'); svg.setAttribute('width', 720); svg.setAttribute('height', 720); let a = 0;
            const cs = ord.map((x, i) => { const f = x.importo_anno_corrente / T0, c = document.createElementNS(NS, 'circle'); c.setAttribute('class', 'seg'); Object.entries({ cx, cy, r: R, fill: 'none', stroke: cols[i], 'stroke-width': W, transform: `rotate(-90 ${cx} ${cy})`, 'stroke-dasharray': `0 ${L}`, 'stroke-dashoffset': -a * L }).forEach(([k, v]) => c.setAttribute(k, v)); c.dataset.f = f; a += f; svg.append(c); return c });
            r.append(svg); const ct = centre(r, cx, cy, 'Totale');
            const rows = ord.map((x, i) => { const e = E(`<div class="row" style="height:56px;top:${540 + i * 66}px"><i style="background:${cols[i]}"></i><img src="${x.icona}" style="height:28px" onerror="this.style.display='none'"><span>${x.nome} · <b style="font-size:14px">${Math.round(x.importo_anno_corrente / T0 * 1000) / 10}%</b></span><b>${fmt(x.importo_anno_corrente)}</b></div>`); r.append(e); return e });
            const tm = [];
            return { steps: 0, show() { tm.forEach(clearTimeout); tm.length = 0; cs.forEach(c => c.setAttribute('stroke-dasharray', `0 ${L}`)); rows.forEach(e => e.classList.remove('on')); ct._v = 0; ct.textContent = fmt(0); tm.push(setTimeout(() => counter(ct, T0, 1600), 300)); cs.forEach((c, i) => tm.push(setTimeout(() => { c.setAttribute('stroke-dasharray', `${Math.max(0, c.dataset.f * L - 3)} ${L}`); rows[i].classList.add('on') }, 400 + i * 300))) } }
        }
    };

    /* ---- ritocchi alle slide esistenti (dopo la costruzione) ---- */
    const lista = (r) => { const rows = [...r.querySelectorAll('.row')], g = Math.min(150, (SH - 200) / (rows.length || 1)); rows.forEach((e, i) => { e.style.top = (110 + i * g) + 'px'; e.style.height = (g - 18) + 'px' }) };
    const POST = {
        slide_ordine_del_giorno: (x, r) => lista(r),
        slide_avanzamento_odg: (x, r) => lista(r),
        slide_composizione_organo: (x, r) => {
            const n = x.persone.length, cs = [...r.querySelectorAll(':scope>.card')], hh = Math.min(220, (SH - 300) / n - 16);
            cs.slice(0, n).forEach((e, i) => { e.classList.add('og'); e.style.left = '30px'; e.style.width = '660px'; e.style.top = (110 + i * (hh + 16)) + 'px'; e.style.height = hh + 'px' });
            if (cs.length > n) { const e = cs[n]; e.style.left = '30px'; e.style.width = '660px'; e.style.top = (110 + n * (hh + 16)) + 'px' }
        },
        slide_quorum_convocazioni: (x, r, D) => {
            const cm = !!D.censimenti, cs = [...r.querySelectorAll(':scope>.card')], k = x.convocazioni.length, hh = cm ? 270 : 380;
            cs.slice(0, k).forEach((e, i) => { e.style.left = '30px'; e.style.width = '660px'; e.style.height = hh + 'px'; e.style.top = (96 + i * (hh + 20)) + 'px' });
            if (cm) {
                const pn = cs[cs.length - 1]; pn.style.cssText += `;left:30px;width:660px;height:560px;top:${96 + k * (hh + 20)}px`;
                const w = E('<div style="position:absolute;left:-400px;top:272px"></div>');
                [...pn.children].filter(c => parseFloat(c.style.left) >= 400).forEach(c => { if (c.style.width === '1px') c.remove(); else w.append(c) }); pn.append(w);
            }
        },
        slide_rendiconto_andamento_avanzo: (x, r) => shrink(r, .54, 150)
    };
    const FALL = ['slide_differenze', 'slide_area_obiettivi', 'slide_cambio_date'];   /* mostrate in scala, finché non hanno una versione verticale */
    const K = { slide_rendiconto_entrate_uscite: 'cs', slide_rendiconto_scontrino_esito: 'cm' };

    Object.keys(T).forEach(k => {
        const o = T[k];
        T[k] = x => { const f = o(x); return (r, D) => { if (!PM) return f(r, D); if (PORT[k]) return PORT[k](x)(r, D); r.classList.add('p-' + (K[k] || 'x')); const g = f(r, D); if (POST[k]) POST[k](x, r, D); if (FALL.includes(k)) shrink(r, .5625, 110); return g } }
    });

    /* il PDF si esporta sempre in formato orizzontale */
    const ex = exportPdf; exportPdf = function () { const p = PM; PM = false; const q = ex(); PM = p; return q };

    if (window.DATI && scenes.length && PM) start(window.DATI);
})();