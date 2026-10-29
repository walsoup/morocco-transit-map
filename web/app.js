/**
 * Réseau urbain Rabat – Salé – Témara
 * Interactive plan for ALSA Citybus and the STRS tramway, from OpenStreetMap.
 *
 * Line colours are NOT the ETL defaults. They are re-mapped onto the palette
 * printed on the official STRS platform map (see ../20261028_*.jpg): T1 runs
 * indigo, T2 runs rose, and bus lines take the amber / olive / blue / plum /
 * rose chips used on the printed interchange blocks.
 */

document.addEventListener('DOMContentLoaded', () => {
  // =========================================================================
  // The printed network's own colours
  // =========================================================================
  const TRAM_T1 = '#3B3160';
  const TRAM_T2 = '#C85F7E';
  const BUS_CHIPS = [
    '#D9541E', '#C0392B', '#E08A2B', '#DDB438', '#6E9B3F', '#3E7A5E',
    '#4A8C8C', '#3B6FA0', '#2F4A7C', '#5A4A8C', '#7B4B94', '#B0455F',
    '#8C6239', '#2F4A7C', '#6E9B3F', '#D9541E'
  ];

  const INK = '#191E1C';
  const PAPER = '#F4EFE3';
  const PAPER_3 = '#E4DBC7';

  const isTramRef = (ref) => ref === 'T1' || ref === 'T2';

  /** Deterministic: the same line keeps the same chip across reloads. */
  function colorFor(ref) {
    if (ref === 'T1') return TRAM_T1;
    if (ref === 'T2') return TRAM_T2;
    let h = 0;
    for (let i = 0; i < ref.length; i++) h = (h * 31 + ref.charCodeAt(i)) >>> 0;
    return BUS_CHIPS[h % BUS_CHIPS.length];
  }

  const state = {
    routesData: null,
    stopsData: null,
    summaryData: null,
    selectedLineRef: null,
    selectedRouteFeature: null,
    activeDirectionIndex: 0,
    activeZone: 'all',
    searchQuery: '',
    radarActive: false,
    radarMarker: null,
    radarCircle: null
  };

  const el = {
    map: document.getElementById('map'),
    sidebar: document.getElementById('sidebar'),
    linesList: document.getElementById('linesList'),
    matchingLinesCount: document.getElementById('matchingLinesCount'),
    searchInput: document.getElementById('searchInput'),
    clearSearchBtn: document.getElementById('clearSearchBtn'),
    zoneChips: document.getElementById('zoneChips'),
    activeLinePanel: document.getElementById('activeLinePanel'),
    linesListContainer: document.getElementById('linesListContainer'),
    activeLineTitle: document.getElementById('activeLineTitle'),
    activeLineDist: document.getElementById('activeLineDist'),
    activeLineStopCount: document.getElementById('activeLineStopCount'),
    directionTabs: document.getElementById('directionTabs'),
    stopsTimeline: document.getElementById('stopsTimeline'),
    closeActiveLineBtn: document.getElementById('closeActiveLineBtn'),
    fitRouteBoundsBtn: document.getElementById('fitRouteBoundsBtn'),
    toggleAllRoutesMapBtn: document.getElementById('toggleAllRoutesMapBtn'),
    toggleStopsCheckbox: document.getElementById('toggleStopsCheckbox'),
    toggleRoutesCheckbox: document.getElementById('toggleRoutesCheckbox'),
    toggleTramCheckbox: document.getElementById('toggleTramCheckbox'),
    basemapSelect: document.getElementById('basemapSelect'),
    radarBtn: document.getElementById('radarBtn'),
    radarBanner: document.getElementById('radarBanner'),
    cancelRadarBtn: document.getElementById('cancelRadarBtn'),
    statsModalBtn: document.getElementById('statsModalBtn'),
    statsModal: document.getElementById('statsModal'),
    closeAnalyticsBtn: document.getElementById('closeAnalyticsBtn'),
    sidebarBackdrop: document.getElementById('sidebarBackdrop'),
    hubMatrix: document.getElementById('hubMatrix'),
    mobileSidebarToggle: document.getElementById('mobileSidebarToggle'),
    statLinesCount: document.getElementById('statLinesCount'),
    statRoutesCount: document.getElementById('statRoutesCount'),
    statStopsCount: document.getElementById('statStopsCount'),
    statKmCount: document.getElementById('statKmCount')
  };

  // =========================================================================
  // Map — honour the basemap the select actually shows (browsers restore
  // form state across reloads, so the attribute-selected option can drift).
  // =========================================================================
  const map = L.map('map', { center: [33.99, -6.84], zoom: 12, zoomControl: false });
  L.control.zoom({ position: 'bottomright' }).addTo(map);

  const tileLayers = {
    positron: L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/">CARTO</a>'
    }),
    dark: L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/">CARTO</a>'
    }),
    osm: L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }),
    satellite: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
      maxZoom: 19,
      attribution: 'Tiles &copy; Esri'
    })
  };

  let currentBasemap = (tileLayers[el.basemapSelect.value] || tileLayers.positron).addTo(map);

  el.basemapSelect.addEventListener('change', (e) => {
    map.removeLayer(currentBasemap);
    currentBasemap = tileLayers[e.target.value] || tileLayers.positron;
    currentBasemap.addTo(map);
  });

  const busRoutesLayerGroup = L.layerGroup().addTo(map);
  const tramRoutesLayerGroup = L.layerGroup().addTo(map);
  const stopsLayerGroup = L.layerGroup().addTo(map);

  const routeLayersMap = new Map();  // relation_id -> L.geoJSON
  const lineRoutesMap = new Map();   // line_ref -> route features
  const stopMarkersMap = new Map();  // stop_id -> L.circleMarker
  const stopLinesMap = new Map();    // stop_id -> [line refs] (for interchanges)
  const stopArabicMap = new Map();   // stop_id -> Arabic name, where OSM has one

  // =========================================================================
  // Data
  // =========================================================================
  async function loadData() {
    try {
      const [routesRes, stopsRes, summaryRes] = await Promise.all([
        fetch('data/rabat_bus_routes.geojson'),
        fetch('data/rabat_bus_stops.geojson'),
        fetch('data/network_summary.json')
      ]);

      state.routesData = await routesRes.json();
      state.stopsData = await stopsRes.json();
      state.summaryData = await summaryRes.json();

      updateSummaryUI();
      indexData();
      renderAllRoutesOnMap();
      renderAllStopsOnMap();
      renderLinesList();
    } catch (err) {
      console.error('Chargement des données impossible :', err);
      const errBanner = document.createElement('div');
      errBanner.style.cssText = 'position:fixed;top:16px;left:50%;transform:translateX(-50%);background:#9D0208;color:#fff;padding:12px 20px;border-radius:6px;font-size:13px;font-weight:600;z-index:9999;box-shadow:0 4px 12px rgba(0,0,0,0.3);';
      errBanner.textContent = 'Erreur lors du chargement des données de transit. Veuillez vérifier la connexion au serveur.';
      document.body.appendChild(errBanner);
    }
  }

  function updateSummaryUI() {
    const m = state.summaryData && state.summaryData.metadata;
    if (!m) return;

    el.statLinesCount.textContent = m.total_lines;
    el.statRoutesCount.textContent = m.total_routes_directions;
    el.statStopsCount.textContent = m.total_unique_stops.toLocaleString('fr-FR');
    el.statKmCount.textContent = Math.round(m.total_network_km).toLocaleString('fr-FR');

    document.getElementById('anTotalLines').textContent = m.total_lines;
    document.getElementById('anTotalStops').textContent = m.total_unique_stops.toLocaleString('fr-FR');
    document.getElementById('anTotalKm').textContent = Math.round(m.total_network_km).toLocaleString('fr-FR');
    document.getElementById('anAvgKm').textContent =
      (m.total_network_km / m.total_routes_directions).toFixed(1).replace('.', ',');
  }

  function indexData() {
    lineRoutesMap.clear();
    state.routesData.features.forEach((feat) => {
      const ref = feat.properties.line_ref;
      if (!lineRoutesMap.has(ref)) lineRoutesMap.set(ref, []);
      lineRoutesMap.get(ref).push(feat);
    });

    stopLinesMap.clear();
    stopArabicMap.clear();
    state.stopsData.features.forEach((f) => {
      const p = f.properties;
      stopLinesMap.set(p.stop_id, p.lines || []);
      const ar = (p.stop_name_ar || '').trim();
      if (ar) stopArabicMap.set(p.stop_id, ar);
    });
  }

  // =========================================================================
  // Map rendering — ribbons and station dots
  // =========================================================================
  function renderAllRoutesOnMap() {
    busRoutesLayerGroup.clearLayers();
    tramRoutesLayerGroup.clearLayers();
    routeLayersMap.clear();

    state.routesData.features.forEach((feat) => {
      const p = feat.properties;
      const tram = p.mode === 'tram' || isTramRef(p.line_ref);
      const color = colorFor(p.line_ref);

      const poly = L.geoJSON(feat, {
        style: {
          color,
          weight: tram ? 4 : 3,
          opacity: 0.88,
          lineJoin: 'round',
          lineCap: 'round'
        }
      });

      poly.bindTooltip(
        `<b>${p.line_ref}</b> · ${p.name}<br><span style="opacity:.7">${p.length_km} km · ${p.stops_count} arrêts</span>`,
        { sticky: true, className: 'route-tooltip' }
      );

      poly.on('mouseover', () => {
        if (!state.selectedLineRef) poly.setStyle({ weight: tram ? 6 : 5, opacity: 1 });
      });
      poly.on('mouseout', () => {
        if (!state.selectedLineRef) poly.setStyle({ weight: tram ? 4 : 3, opacity: 0.88 });
      });
      poly.on('click', () => selectLine(p.line_ref));

      routeLayersMap.set(p.relation_id, poly);
      (tram ? tramRoutesLayerGroup : busRoutesLayerGroup).addLayer(poly);
    });
  }

  /** Unselected stops read as open dots, exactly like the printed plan. */
  function stopBaseStyle(feat) {
    const isTram = feat.properties.is_tram;
    const ref = isTram ? (feat.properties.lines || []).find(isTramRef) : null;
    return isTram
      ? { radius: 4.5, fillColor: colorFor(ref || 'T2'), color: INK, weight: 1.2, fillOpacity: 1, opacity: 1 }
      : { radius: 3.6, fillColor: PAPER, color: INK, weight: 1.6, fillOpacity: 1, opacity: 1 };
  }

  function renderAllStopsOnMap() {
    stopsLayerGroup.clearLayers();
    stopMarkersMap.clear();

    state.stopsData.features.forEach((feat) => {
      const p = feat.properties;
      const [lon, lat] = feat.geometry.coordinates;

      const marker = L.circleMarker([lat, lon], stopBaseStyle(feat));
      marker.bindPopup(() => createStopPopup(p, [lat, lon]));
      marker.on('click', () => marker.openPopup());

      stopMarkersMap.set(p.stop_id, marker);
      stopsLayerGroup.addLayer(marker);
    });
  }

  // =========================================================================
  // Stop plate — ink header band + chip grid
  // =========================================================================
  function lineChipButton(ref) {
    const b = document.createElement('button');
    b.style.backgroundColor = colorFor(ref);
    b.textContent = ref;
    b.setAttribute('data-line', ref);
    b.title = `Ligne ${ref}`;
    return b;
  }

  function stopKindLabel(p) {
    const hasBus = (p.lines || []).some((l) => !isTramRef(l));
    if (p.is_tram && hasBus) return 'Correspondance tramway & bus';
    if (p.is_tram) return 'Station STRS';
    return 'Arrêt ALSA Citybus';
  }

  function createStopPopup(p, coords) {
    const div = document.createElement('div');
    div.className = 'custom-popup';

    const band = document.createElement('div');
    band.className = 'popup-band';
    band.textContent = p.stop_name;
    if (p.stop_name_ar && p.stop_name_ar.trim()) {
      const ar = document.createElement('span');
      ar.className = 'popup-ar arabic-text';
      ar.dir = 'rtl';
      ar.textContent = p.stop_name_ar;
      band.appendChild(ar);
    }

    const body = document.createElement('div');
    body.className = 'popup-body';

    const kind = document.createElement('div');
    kind.className = 'popup-kind';
    kind.textContent = `${stopKindLabel(p)} · ${(p.lines || []).length} ligne${(p.lines || []).length > 1 ? 's' : ''}`;
    body.appendChild(kind);

    const grid = document.createElement('div');
    grid.className = 'popup-grid';
    (p.lines || []).forEach((ref) => {
      const btn = lineChipButton(ref);
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        map.closePopup();
        selectLine(ref);
      });
      grid.appendChild(btn);
    });
    body.appendChild(grid);

    const c = document.createElement('div');
    c.className = 'popup-coords';
    const latHem = coords[0] >= 0 ? 'N' : 'S';
    const lonHem = coords[1] >= 0 ? 'E' : 'O';
    c.textContent = `${Math.abs(coords[0]).toFixed(5)}° ${latHem} · ${Math.abs(coords[1]).toFixed(5)}° ${lonHem}`;
    body.appendChild(c);

    div.appendChild(band);
    div.appendChild(body);
    return div;
  }

  // =========================================================================
  // Line list
  // =========================================================================
  function renderLinesList() {
    el.linesList.innerHTML = '';
    const query = state.searchQuery.toLowerCase().trim();
    const zone = state.activeZone;

    const refs = Array.from(lineRoutesMap.keys()).sort((a, b) => {
      const ta = isTramRef(a), tb = isTramRef(b);
      if (ta !== tb) return ta ? -1 : 1;
      const na = parseInt(a.replace(/\D/g, ''), 10);
      const nb = parseInt(b.replace(/\D/g, ''), 10);
      if (!isNaN(na) && !isNaN(nb) && na !== nb) return na - nb;
      return a.localeCompare(b, 'fr');
    });

    let shown = 0;

    refs.forEach((ref) => {
      const routes = lineRoutesMap.get(ref);
      const p0 = routes[0].properties;
      const tram = p0.mode === 'tram' || isTramRef(ref);

      const matchesZone = zone === 'all' || p0.zone === zone || (zone === 'Tramway' && tram);
      const matchesSearch = !query ||
        ref.toLowerCase().includes(query) ||
        routes.some((r) => {
          const rp = r.properties;
          return (
            (rp.name && rp.name.toLowerCase().includes(query)) ||
            (rp.origin && rp.origin.toLowerCase().includes(query)) ||
            (rp.destination && rp.destination.toLowerCase().includes(query))
          );
        });

      if (!matchesZone || !matchesSearch) return;
      shown++;

      const row = document.createElement('div');
      row.className = 'row' + (state.selectedLineRef === ref ? ' active' : '');
      row.setAttribute('data-ref', ref);
      row.setAttribute('tabindex', '0');
      row.setAttribute('role', 'button');

      const marker = document.createElement(tram ? 'div' : 'div');
      marker.className = tram ? 'disc' : 'chip';
      marker.style.backgroundColor = colorFor(ref);
      marker.textContent = ref;

      const dest = document.createElement('div');
      dest.className = 'dest';
      const fr = document.createElement('div');
      fr.className = 'fr';
      fr.textContent = `${p0.origin} → ${p0.destination}`;
      dest.appendChild(fr);

      const km = document.createElement('div');
      km.className = 'km';
      const b = document.createElement('b');
      b.textContent = String(p0.length_km);
      km.appendChild(b);
      km.appendChild(document.createTextNode('km'));

      row.appendChild(marker);
      row.appendChild(dest);
      row.appendChild(km);

      row.addEventListener('click', () => selectLine(ref));
      row.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectLine(ref); }
      });

      el.linesList.appendChild(row);
    });

    el.matchingLinesCount.textContent = `${shown} ligne${shown > 1 ? 's' : ''}`;
  }

  el.searchInput.addEventListener('input', (e) => {
    state.searchQuery = e.target.value;
    el.clearSearchBtn.style.display = state.searchQuery ? 'flex' : 'none';
    renderLinesList();
  });

  el.clearSearchBtn.addEventListener('click', () => {
    el.searchInput.value = '';
    state.searchQuery = '';
    el.clearSearchBtn.style.display = 'none';
    renderLinesList();
    el.searchInput.focus();
  });

  // Zone plates
  el.zoneChips.querySelectorAll('.plate').forEach((plate) => {
    plate.addEventListener('click', () => {
      el.zoneChips.querySelectorAll('.plate').forEach((p) => p.classList.remove('on'));
      plate.classList.add('on');
      state.activeZone = plate.getAttribute('data-zone');
      renderLinesList();
    });
  });

  // =========================================================================
  // Active line: band + ribbon
  // =========================================================================
  function selectLine(ref) {
    const routes = lineRoutesMap.get(ref);
    if (!routes || !routes.length) return;

    state.selectedLineRef = ref;
    state.activeDirectionIndex = 0;
    const active = routes[0];
    state.selectedRouteFeature = active;

    el.linesListContainer.style.display = 'none';
    el.activeLinePanel.style.display = 'flex';

    el.activeLineTitle.textContent = `${active.properties.origin} → ${active.properties.destination}`;
    el.activeLineDist.textContent = `${active.properties.length_km} km`;
    el.activeLineStopCount.textContent = `${active.properties.stops_count} arrêts`;

    renderDirectionPlates(routes);
    renderRibbon(active);
    highlightRoute(active);

    if (window.innerWidth <= 900) toggleMobileSidebar(false);
  }

  function renderDirectionPlates(routes) {
    el.directionTabs.innerHTML = '';
    routes.forEach((r, idx) => {
      const b = document.createElement('button');
      b.className = 'dir-plate' + (idx === state.activeDirectionIndex ? ' on' : '');
      b.textContent = `→ ${r.properties.destination}`;
      b.title = r.properties.name;

      b.addEventListener('click', () => {
        state.activeDirectionIndex = idx;
        state.selectedRouteFeature = r;
        el.activeLineTitle.textContent = `${r.properties.origin} → ${r.properties.destination}`;
        el.activeLineDist.textContent = `${r.properties.length_km} km`;
        el.activeLineStopCount.textContent = `${r.properties.stops_count} arrêts`;
        renderDirectionPlates(routes);
        renderRibbon(r);
        highlightRoute(r);
      });

      el.directionTabs.appendChild(b);
    });
  }

  /** The route ribbon: terminus plates at both ends, interchanges marked. */
  function renderRibbon(routeFeat) {
    const ref = routeFeat.properties.line_ref;
    el.stopsTimeline.innerHTML = '';
    el.stopsTimeline.style.setProperty('--line-color', colorFor(ref));

    const stops = routeFeat.properties.stops || [];
    const last = stops.length - 1;

    stops.forEach((s, idx) => {
      const li = document.createElement('li');
      li.className = 'step' + (idx === 0 ? ' first' : '') + (idx === last ? ' last' : '');
      li.setAttribute('tabindex', '0');

      const name = document.createElement('span');
      name.className = 'nm';
      name.textContent = s.name;

      if (idx === 0 || idx === last) {
        const plate = document.createElement('span');
        plate.className = 'term-plate';
        plate.textContent = idx === 0 ? 'Origine' : 'Terminus';
        li.appendChild(plate);
        li.appendChild(name);
      } else {
        li.appendChild(name);

        // A stop served by 3+ lines is a real interchange on this network.
        const via = stopLinesMap.get(s.id) || [];
        if (via.length >= 3) {
          li.classList.add('interchange');
          const chips = document.createElement('span');
          chips.className = 'xchips';
          via.slice(0, 5).forEach((l) => {
            const c = document.createElement('span');
            c.style.backgroundColor = colorFor(l);
            c.textContent = l;
            chips.appendChild(c);
          });
          li.appendChild(chips);

          const sub = document.createElement('span');
          sub.className = 'sub';
          sub.textContent = ` ${via.length} lignes`;
          li.appendChild(sub);
        }
      }

      const flyTo = () => {
        const marker = stopMarkersMap.get(s.id);
        if (marker) {
          map.flyTo(marker.getLatLng(), 16, { duration: 0.8 });
          marker.openPopup();
        }
      };

      li.addEventListener('click', flyTo);
      li.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); flyTo(); }
      });

      el.stopsTimeline.appendChild(li);
    });
  }

  function highlightRoute(routeFeat) {
    const activeId = routeFeat.properties.relation_id;
    const activeColor = colorFor(routeFeat.properties.line_ref);

    routeLayersMap.forEach((poly, id) => {
      if (id === activeId) {
        poly.setStyle({ color: activeColor, weight: 6, opacity: 1 });
        poly.bringToFront();
      } else {
        poly.setStyle({ opacity: 0.1, weight: 2 });
      }
    });

    const onRoute = new Set((routeFeat.properties.stops || []).map((s) => s.id));
    stopMarkersMap.forEach((marker, id) => {
      if (onRoute.has(id)) {
        marker.setStyle({ radius: 5, fillColor: activeColor, color: INK, weight: 1.6, fillOpacity: 1, opacity: 1 });
      } else {
        marker.setStyle({ radius: 2.6, fillColor: PAPER_3, color: INK, weight: 1, fillOpacity: 0.7, opacity: 0.7 });
      }
    });

    const poly = routeLayersMap.get(activeId);
    if (poly) map.fitBounds(poly.getBounds(), { padding: [40, 40], maxZoom: 14 });
  }

  function resetToAllRoutes() {
    state.selectedLineRef = null;
    state.selectedRouteFeature = null;

    el.activeLinePanel.style.display = 'none';
    el.linesListContainer.style.display = 'flex';

    routeLayersMap.forEach((poly) => poly.setStyle({ weight: 3, opacity: 0.88 }));
    stopMarkersMap.forEach((marker, id) => {
      const feat = state.stopsData.features.find((f) => f.properties.stop_id === id);
      if (feat) marker.setStyle(stopBaseStyle(feat));
    });

    document.querySelectorAll('.row').forEach((r) => r.classList.remove('active'));
    map.flyTo([33.99, -6.84], 12);
  }

  el.closeActiveLineBtn.addEventListener('click', resetToAllRoutes);
  el.toggleAllRoutesMapBtn.addEventListener('click', resetToAllRoutes);
  el.fitRouteBoundsBtn.addEventListener('click', () => {
    if (state.selectedRouteFeature) {
      const poly = routeLayersMap.get(state.selectedRouteFeature.properties.relation_id);
      if (poly) map.fitBounds(poly.getBounds(), { padding: [40, 40], maxZoom: 14 });
    }
  });

  // =========================================================================
  // Layer plates
  // =========================================================================
  el.toggleRoutesCheckbox.addEventListener('change', (e) => {
    if (e.target.checked) map.addLayer(busRoutesLayerGroup); else map.removeLayer(busRoutesLayerGroup);
  });

  el.toggleTramCheckbox.addEventListener('change', (e) => {
    if (e.target.checked) map.addLayer(tramRoutesLayerGroup); else map.removeLayer(tramRoutesLayerGroup);
  });

  el.toggleStopsCheckbox.addEventListener('change', (e) => {
    if (e.target.checked) map.addLayer(stopsLayerGroup); else map.removeLayer(stopsLayerGroup);
  });

  // =========================================================================
  // Arrêt le plus proche
  // =========================================================================
  function activateRadar() {
    state.radarActive = true;
    el.radarBanner.style.display = 'flex';
    el.radarBtn.textContent = 'Annuler la recherche';

    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        (pos) => findNearest(pos.coords.latitude, pos.coords.longitude, 'Votre position (GPS)'),
        (err) => {
          console.warn('Géolocalisation indisponible ou refusée :', err.message);
          const bannerText = el.radarBanner.querySelector('span:not(.radar-pulse)');
          if (bannerText) bannerText.textContent = 'GPS non accessible. Cliquez directement sur la carte pour chercher les arrêts.';
        },
        { timeout: 6000 }
      );
    }
  }

  function deactivateRadar() {
    state.radarActive = false;
    el.radarBanner.style.display = 'none';
    el.radarBtn.innerHTML =
      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="12" cy="12" r="9"></circle><circle cx="12" cy="12" r="3"></circle><line x1="21" y1="12" x2="17" y2="12"></line><line x1="7" y1="12" x2="3" y2="12"></line><line x1="12" y1="7" x2="12" y2="3"></line><line x1="12" y1="21" x2="12" y2="17"></line></svg> Arrêt le plus proche';

    [state.radarMarker, state.radarCircle].forEach((l) => { if (l) map.removeLayer(l); });
    state.radarMarker = null;
    state.radarCircle = null;
  }

  el.radarBtn.addEventListener('click', () => {
    if (state.radarActive) deactivateRadar(); else activateRadar();
  });
  el.cancelRadarBtn.addEventListener('click', deactivateRadar);

  map.on('click', (e) => {
    if (state.radarActive) findNearest(e.latlng.lat, e.latlng.lng, 'Position sélectionnée');
  });

  function findNearest(lat, lon, label) {
    if (state.radarMarker) map.removeLayer(state.radarMarker);
    if (state.radarCircle) map.removeLayer(state.radarCircle);

    state.radarMarker = L.marker([lat, lon], {
      icon: L.divIcon({
        className: 'radar-pin',
        html: `<div style="width:12px;height:12px;background:#D9541E;border:2px solid ${INK};"></div>`,
        iconSize: [12, 12],
        iconAnchor: [6, 6]
      })
    }).addTo(map);

    state.radarCircle = L.circle([lat, lon], {
      radius: 600,
      color: '#D9541E',
      weight: 1,
      fillColor: '#D9541E',
      fillOpacity: 0.06
    }).addTo(map);

    const scored = state.stopsData.features
      .map((f) => {
        const [sLon, sLat] = f.geometry.coordinates;
        return { feat: f, dist: haversineMeters([lon, lat], [sLon, sLat]) };
      })
      .sort((a, b) => a.dist - b.dist);

    const withinRadius = scored.filter((item) => item.dist <= 600);
    const nearest = (withinRadius.length > 0 ? withinRadius : scored).slice(0, 5);
    const isWithin = withinRadius.length > 0;

    const div = document.createElement('div');
    div.className = 'custom-popup';

    const band = document.createElement('div');
    band.className = 'popup-band';
    band.textContent = label;

    const body = document.createElement('div');
    body.className = 'popup-body';

    const kind = document.createElement('div');
    kind.className = 'popup-kind';
    kind.textContent = isWithin
      ? `Arrêts à proximité · rayon 600 m (${nearest.length})`
      : `Arrêts les plus proches (${nearest.length})`;
    body.appendChild(kind);

    const ul = document.createElement('ul');
    ul.className = 'popup-radar-list';
    nearest.forEach((item) => {
      const li = document.createElement('li');
      li.style.display = 'flex';
      li.style.flexDirection = 'column';
      li.style.gap = '2px';
      li.style.cursor = 'pointer';

      const rowTop = document.createElement('div');
      rowTop.style.display = 'flex';
      rowTop.style.justifyContent = 'space-between';
      rowTop.style.alignItems = 'center';

      const nm = document.createElement('span');
      nm.style.fontWeight = '600';
      nm.textContent = item.feat.properties.stop_name;

      const d = document.createElement('span');
      d.className = 'd';
      d.textContent = `${Math.round(item.dist)} m`;

      rowTop.appendChild(nm);
      rowTop.appendChild(d);
      li.appendChild(rowTop);

      const lines = item.feat.properties.lines || [];
      if (lines.length > 0) {
        const lineWrap = document.createElement('div');
        lineWrap.style.display = 'flex';
        lineWrap.style.flexWrap = 'wrap';
        lineWrap.style.gap = '3px';
        lineWrap.style.marginTop = '2px';

        lines.slice(0, 6).forEach((l) => {
          const chip = document.createElement('span');
          chip.style.backgroundColor = colorFor(l);
          chip.style.color = '#fff';
          chip.style.fontSize = '9px';
          chip.style.fontWeight = '700';
          chip.style.padding = '1px 4px';
          chip.style.borderRadius = '2px';
          chip.textContent = l;
          lineWrap.appendChild(chip);
        });
        if (lines.length > 6) {
          const more = document.createElement('span');
          more.style.fontSize = '9px';
          more.style.opacity = '0.7';
          more.textContent = `+${lines.length - 6}`;
          lineWrap.appendChild(more);
        }
        li.appendChild(lineWrap);
      }

      li.addEventListener('click', (e) => {
        e.stopPropagation();
        map.closePopup();
        const m = stopMarkersMap.get(item.feat.properties.stop_id);
        if (m) { map.flyTo(m.getLatLng(), 16, { duration: 0.8 }); m.openPopup(); }
      });
      ul.appendChild(li);
    });
    body.appendChild(ul);

    div.appendChild(band);
    div.appendChild(body);

    state.radarMarker.bindPopup(div).openPopup();
    map.flyTo([lat, lon], 15, { duration: 0.6 });
  }

  function haversineMeters(p1, p2) {
    const [lon1, lat1] = p1;
    const [lon2, lat2] = p2;
    const R = 6371000;
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat / 2) ** 2 +
      Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  // =========================================================================
  // Réseau & données — the interchange matrix
  // =========================================================================
  function openAnalytics() { el.statsModal.style.display = 'flex'; }
  function closeAnalytics() { el.statsModal.style.display = 'none'; }

  function renderHubMatrix() {
    const m = state.summaryData && state.summaryData.metadata;
    if (!m) return;
    el.hubMatrix.innerHTML = '';

    (m.top_hubs || []).forEach((hub) => {
      const block = document.createElement('div');
      block.className = 'block';

      const head = document.createElement('div');
      head.className = 'head';
      head.textContent = hub.name;
      // Arabic only when OSM actually carries it for this stop — never faked.
      const ar = stopArabicMap.get(hub.stop_id);
      if (ar) {
        const span = document.createElement('span');
        span.className = 'ar arabic-text';
        span.dir = 'rtl';
        span.textContent = ar;
        head.appendChild(span);
      }

      const grid = document.createElement('div');
      grid.className = 'grid';
      (hub.lines || []).forEach((l) => {
        const b = document.createElement('button');
        b.style.backgroundColor = colorFor(l);
        b.textContent = l;
        b.title = `Ligne ${l} — voir l’itinéraire`;
        b.addEventListener('click', () => {
          closeAnalytics();
          selectLine(l);
        });
        grid.appendChild(b);
      });

      block.appendChild(head);
      block.appendChild(grid);
      el.hubMatrix.appendChild(block);
    });
  }

  el.statsModalBtn.addEventListener('click', () => { renderHubMatrix(); openAnalytics(); });
  el.closeAnalyticsBtn.addEventListener('click', closeAnalytics);
  el.statsModal.addEventListener('click', (e) => { if (e.target === el.statsModal) closeAnalytics(); });

  // =========================================================================
  // Mobile drawer
  // =========================================================================
  function toggleMobileSidebar(force) {
    const isOpen = el.sidebar.classList.toggle('open', force);
    if (el.sidebarBackdrop) {
      el.sidebarBackdrop.classList.toggle('show', isOpen);
    }
  }

  el.mobileSidebarToggle.addEventListener('click', () => toggleMobileSidebar());
  if (el.sidebarBackdrop) {
    el.sidebarBackdrop.addEventListener('click', () => toggleMobileSidebar(false));
  }

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (el.statsModal.style.display === 'flex') closeAnalytics();
    else if (state.radarActive) deactivateRadar();
    else if (state.selectedLineRef) resetToAllRoutes();
  });

  loadData();
});
