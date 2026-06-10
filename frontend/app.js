(function () {
    'use strict';

    var API_BASE = '/api/v1';
    var CZML_ENDPOINT = API_BASE + '/czml/stream';
    var STATS_ENDPOINT = API_BASE + '/statistics';
    var ALERTS_ENDPOINT = API_BASE + '/collisions';
    var REFRESH_INTERVAL_MS = 30000;

    var viewer = null;
    var czmlDataSource = null;
    var clockMultiplier = 60;
    var isPlaying = true;
    var refreshTimer = null;
    var statsData = {
        total: 0,
        debris: 0,
        warnings: 0,
        tracking: 0,
        orbits: { leo: 0, meo: 0, geo: 0, heo: 0 }
    };

    function init() {
        initCesium();
        bindControls();
        startClock();
        updateCurrentTime();
        setInterval(updateCurrentTime, 1000);
        loadInitialData();
    }

    function initCesium() {
        Cesium.Ion.defaultAccessToken = undefined;

        viewer = new Cesium.Viewer('cesiumViewer', {
            imageryProvider: new Cesium.TileMapServiceImageryProvider({
                url: Cesium.buildModuleUrl('Assets/Textures/NaturalEarthII')
            }),
            baseLayerPicker: false,
            geocoder: false,
            homeButton: true,
            sceneModePicker: true,
            navigationHelpButton: false,
            animation: false,
            timeline: false,
            fullscreenButton: false,
            selectionIndicator: true,
            infoBox: false,
            skyBox: false,
            skyAtmosphere: new Cesium.SkyAtmosphere(),
            requestRenderMode: true,
            maximumRenderTimeChange: Infinity,
        });

        viewer.scene.backgroundColor = Cesium.Color.fromCssColorString('#0a0e17');
        viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString('#0d1321');
        viewer.scene.globe.enableLighting = true;
        viewer.scene.fog.enabled = true;
        viewer.scene.fog.density = 0.0002;

        czmlDataSource = new Cesium.CzmlDataSource('satellites');
        viewer.dataSources.add(czmlDataSource);

        var handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
        handler.setInputAction(onEntityClick, Cesium.ScreenSpaceEventType.LEFT_CLICK);

        viewer.clock.onTick.addEventListener(onClockTick);
    }

    function onEntityClick(click) {
        var picked = viewer.scene.pick(click.position);
        if (!Cesium.defined(picked) || !Cesium.defined(picked.id)) {
            hideInfoBox();
            return;
        }

        var entity = picked.id;
        if (!entity || !entity.position) return;

        var props = entity.properties || {};
        var name = entity.name || '未知';
        var noradId = getProperty(props, 'noradId', '--');
        var inclination = getProperty(props, 'inclination', '--');
        var altitude = getProperty(props, 'altitude', '--');
        var period = getProperty(props, 'period', '--');
        var eccentricity = getProperty(props, 'eccentricity', '--');

        showInfoBox(name, noradId, inclination, altitude, period, eccentricity);
    }

    function getProperty(props, key, fallback) {
        try {
            var val = props[key];
            if (val !== undefined && val !== null) {
                return typeof val.getValue === 'function' ? val.getValue(Cesium.JulianDate.now()) : val;
            }
        } catch (e) {
            // ignore
        }
        return fallback;
    }

    function showInfoBox(name, noradId, inclination, altitude, period, eccentricity) {
        var box = document.getElementById('satellite-info-box');
        document.getElementById('info-box-title').textContent = name;
        document.getElementById('info-name').textContent = name;
        document.getElementById('info-norad').textContent = noradId;
        document.getElementById('info-inclination').textContent = inclination !== '--' ? inclination + '°' : '--';
        document.getElementById('info-altitude').textContent = altitude !== '--' ? altitude + ' km' : '--';
        document.getElementById('info-period').textContent = period !== '--' ? period + ' min' : '--';
        document.getElementById('info-eccentricity').textContent = eccentricity;
        box.classList.remove('hidden');
    }

    function hideInfoBox() {
        document.getElementById('satellite-info-box').classList.add('hidden');
    }

    function onClockTick(clock) {
        updateTimelineDisplay(clock);
    }

    function startClock() {
        var now = new Date();
        var startTime = Cesium.JulianDate.fromDate(new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0));
        var stopTime = Cesium.JulianDate.addHours(startTime, 24, new Cesium.JulianDate());

        viewer.clock.startTime = startTime;
        viewer.clock.stopTime = stopTime;
        viewer.clock.currentTime = Cesium.JulianDate.fromDate(now);
        viewer.clock.multiplier = clockMultiplier;
        viewer.clock.clockRange = Cesium.ClockRange.LOOP_STOP;
        viewer.clock.shouldAnimate = true;

        document.getElementById('sim-time').textContent = formatJulianDate(startTime);
        document.getElementById('sim-time-end').textContent = formatJulianDate(stopTime);
    }

    function updateTimelineDisplay(clock) {
        var current = clock.currentTime;
        var start = clock.startTime;
        var stop = clock.stopTime;

        var totalSeconds = Cesium.JulianDate.secondsDifference(stop, start);
        var elapsedSeconds = Cesium.JulianDate.secondsDifference(current, start);
        var progress = Math.max(0, Math.min(100, (elapsedSeconds / totalSeconds) * 100));

        document.getElementById('timeline-progress').style.width = progress + '%';
        document.getElementById('timeline-marker').style.left = progress + '%';

        var elapsedH = Math.floor(elapsedSeconds / 3600);
        var elapsedM = Math.floor((elapsedSeconds % 3600) / 60);
        var elapsedS = Math.floor(elapsedSeconds % 60);
        document.getElementById('elapsed-time').textContent =
            '已过: ' + elapsedH + ':' + pad(elapsedM) + ':' + pad(elapsedS);
    }

    function formatJulianDate(jd) {
        var d = Cesium.JulianDate.toDate(jd);
        return d.getFullYear() + '-' +
            pad(d.getMonth() + 1) + '-' +
            pad(d.getDate()) + ' ' +
            pad(d.getHours()) + ':' +
            pad(d.getMinutes());
    }

    function pad(n) {
        return n < 10 ? '0' + n : String(n);
    }

    function bindControls() {
        document.getElementById('btn-play').addEventListener('click', togglePlay);
        document.getElementById('btn-rewind').addEventListener('click', function () {
            changeMultiplier(-1);
        });
        document.getElementById('btn-forward').addEventListener('click', function () {
            changeMultiplier(1);
        });
        document.getElementById('speed-selector').addEventListener('change', function (e) {
            clockMultiplier = parseInt(e.target.value, 10);
            viewer.clock.multiplier = clockMultiplier;
        });
        document.getElementById('info-box-close').addEventListener('click', hideInfoBox);

        document.querySelector('.timeline-track').addEventListener('click', function (e) {
            var rect = this.getBoundingClientRect();
            var ratio = (e.clientX - rect.left) / rect.width;
            ratio = Math.max(0, Math.min(1, ratio));
            var start = viewer.clock.startTime;
            var stop = viewer.clock.stopTime;
            var totalSeconds = Cesium.JulianDate.secondsDifference(stop, start);
            var target = Cesium.JulianDate.addSeconds(start, ratio * totalSeconds, new Cesium.JulianDate());
            viewer.clock.currentTime = target;
        });
    }

    function togglePlay() {
        isPlaying = !isPlaying;
        viewer.clock.shouldAnimate = isPlaying;
        var btn = document.getElementById('btn-play');
        btn.innerHTML = isPlaying ? '&#9654;' : '&#9646;&#9646;';
        btn.classList.toggle('active', isPlaying);
    }

    function changeMultiplier(direction) {
        var speeds = [1, 10, 60, 360, 1440];
        var idx = speeds.indexOf(clockMultiplier);
        if (idx === -1) idx = 2;
        idx += direction;
        idx = Math.max(0, Math.min(speeds.length - 1, idx));
        clockMultiplier = speeds[idx];
        viewer.clock.multiplier = clockMultiplier;
        document.getElementById('speed-selector').value = String(clockMultiplier);
    }

    function loadInitialData() {
        loadCzmlData();
        loadStats();
        loadAlerts();
        startAutoRefresh();
    }

    function startAutoRefresh() {
        if (refreshTimer) clearInterval(refreshTimer);
        refreshTimer = setInterval(function () {
            loadCzmlData();
            loadStats();
            loadAlerts();
            document.getElementById('data-refresh-status').textContent =
                '数据刷新: ' + new Date().toLocaleTimeString('zh-CN');
        }, REFRESH_INTERVAL_MS);
    }

    function loadCzmlData() {
        czmlDataSource.load(CZML_ENDPOINT).then(function () {
            updateDashboardFromEntities();
        }).catch(function (error) {
            console.warn('[CZML] Load failed, using demo data:', error.message || error);
            loadDemoCzml();
        });
    }

    function loadDemoCzml() {
        var demoCzml = generateDemoCzml();
        czmlDataSource.load(demoCzml).then(function () {
            updateDashboardFromEntities();
        }).catch(function (e) {
            console.error('[CZML] Demo load failed:', e);
        });
    }

    function generateDemoCzml() {
        var now = Cesium.JulianDate.now();
        var startIso = Cesium.JulianDate.toIso8601(viewer.clock.startTime);
        var stopIso = Cesium.JulianDate.toIso8601(viewer.clock.stopTime);

        var sats = [
            { name: 'ISS (ZARYA)', color: [0, 1, 1], inc: 51.6, alt: 408, norad: 25544 },
            { name: 'HST (HUBBLE)', color: [0, 0.8, 1], inc: 28.5, alt: 547, norad: 20580 },
            { name: 'STARLINK-1007', color: [0, 1, 0.53], inc: 53.0, alt: 550, norad: 44713 },
            { name: 'STARLINK-1021', color: [0, 1, 0.53], inc: 53.0, alt: 550, norad: 44714 },
            { name: 'STARLINK-1045', color: [0, 1, 0.53], inc: 53.0, alt: 540, norad: 44715 },
            { name: 'STARLINK-1060', color: [0, 1, 0.53], inc: 53.0, alt: 560, norad: 44716 },
            { name: 'STARLINK-1082', color: [0, 1, 0.53], inc: 53.0, alt: 545, norad: 44717 },
            { name: 'COSMOS-2251 DEB', color: [1, 0.2, 0.2], inc: 74.0, alt: 790, norad: 34492 },
            { name: 'FENGYUN-1C DEB', color: [1, 0.4, 0.2], inc: 99.0, alt: 850, norad: 30923 },
            { name: 'TERRA', color: [0, 0.53, 1], inc: 98.2, alt: 705, norad: 25994 },
            { name: 'AQUA', color: [0, 0.53, 1], inc: 98.2, alt: 705, norad: 27424 },
            { name: 'LANDSAT-9', color: [0, 0.53, 1], inc: 98.2, alt: 705, norad: 49260 },
            { name: 'SENTINEL-2A', color: [0, 0.53, 1], inc: 98.6, alt: 786, norad: 40697 },
            { name: 'GPS BIIR-2', color: [1, 1, 0], inc: 55.0, alt: 20200, norad: 28474 },
            { name: 'GPS BIIF-5', color: [1, 1, 0], inc: 55.0, alt: 20200, norad: 39533 },
            { name: 'GLONASS-M 48', color: [1, 0.67, 0], inc: 64.8, alt: 19100, norad: 41379 },
            { name: 'GOES-16', color: [1, 0.8, 0], inc: 0.2, alt: 35786, norad: 41866 },
            { name: 'GOES-17', color: [1, 0.8, 0], inc: 0.3, alt: 35786, norad: 43226 },
            { name: 'MOLNIYA 1-93', color: [1, 0.4, 0], inc: 62.8, alt: 500, norad: 25485 },
            { name: 'NOAA-19', color: [0, 0.8, 1], inc: 99.1, alt: 870, norad: 33591 },
        ];

        var czml = [{
            id: 'document',
            name: 'satellite-monitor',
            version: '1.0',
            clock: {
                interval: startIso + '/' + stopIso,
                multiplier: clockMultiplier,
                range: 'LOOP_STOP',
                step: 'SYSTEM_CLOCK_MULTIPLIER'
            }
        }];

        for (var i = 0; i < sats.length; i++) {
            var s = sats[i];
            var semiMajor = 6371 + s.alt;
            var ecc = s.name.indexOf('DEB') !== -1 ? 0.005 : 0.0003 + Math.random() * 0.001;
            czml.push({
                id: 'sat-' + s.norad,
                name: s.name,
                availability: startIso + '/' + stopIso,
                position: {
                    interpolationAlgorithm: 'LAGRANGE',
                    interpolationDegree: 5,
                    referenceFrame: 'INERTIAL',
                    epoch: startIso,
                    cartographicDegrees: generateOrbitPositions(s.inc, semiMajor, ecc, startIso, stopIso)
                },
                point: {
                    pixelSize: s.name.indexOf('DEB') !== -1 ? 5 : 8,
                    color: { rgba: [Math.round(s.color[0] * 255), Math.round(s.color[1] * 255), Math.round(s.color[2] * 255), 255] },
                    outlineColor: { rgba: [0, 0, 0, 128] },
                    outlineWidth: 1
                },
                path: {
                    show: true,
                    width: 1,
                    resolution: 120,
                    material: {
                        solidColor: {
                            color: { rgba: [Math.round(s.color[0] * 255), Math.round(s.color[1] * 255), Math.round(s.color[2] * 255), 80] }
                        }
                    },
                    leadTime: 0,
                    trailTime: Math.round(2 * Math.PI * Math.sqrt(Math.pow(semiMajor, 3) / 398600.4418) / 60)
                },
                properties: {
                    noradId: s.norad,
                    inclination: s.inc,
                    altitude: s.alt,
                    period: Math.round(2 * Math.PI * Math.sqrt(Math.pow(semiMajor, 3) / 398600.4418) / 60),
                    eccentricity: ecc.toFixed(6)
                }
            });
        }

        return czml;
    }

    function generateOrbitPositions(inclination, semiMajorKm, eccentricity, startIso, stopIso) {
        var positions = [];
        var periodSec = 2 * Math.PI * Math.sqrt(Math.pow(semiMajorKm, 3) / 398600.4418);
        var incRad = inclination * Math.PI / 180;
        var raan = Math.random() * 2 * Math.PI;
        var argPerigee = Math.random() * 2 * Math.PI;
        var startMs = Cesium.JulianDate.toDate(Cesium.JulianDate.fromIso8601(startIso)).getTime();
        var stopMs = Cesium.JulianDate.toDate(Cesium.JulianDate.fromIso8601(stopIso)).getTime();
        var stepSec = 120;

        for (var t = 0; t <= (stopMs - startMs) / 1000; t += stepSec) {
            var meanAnomaly = (2 * Math.PI * t) / periodSec;
            var trueAnomaly = solveKepler(meanAnomaly, eccentricity);
            var r = semiMajorKm * (1 - eccentricity * eccentricity) / (1 + eccentricity * Math.cos(trueAnomaly));
            var xOrb = r * Math.cos(trueAnomaly);
            var yOrb = r * Math.sin(trueAnomaly);
            var cosA = Math.cos(argPerigee);
            var sinA = Math.sin(argPerigee);
            var cosO = Math.cos(raan);
            var sinO = Math.sin(raan);
            var cosI = Math.cos(incRad);
            var sinI = Math.sin(incRad);
            var x = (cosO * cosA - sinO * sinA * cosI) * xOrb + (-cosO * sinA - sinO * cosA * cosI) * yOrb;
            var y = (sinO * cosA + cosO * sinA * cosI) * xOrb + (-sinO * sinA + cosO * cosA * cosI) * yOrb;
            var z = (sinA * sinI) * xOrb + (cosA * sinI) * yOrb;
            var lon = Math.atan2(y, x) * 180 / Math.PI;
            var lat = Math.asin(Math.max(-1, Math.min(1, z / r))) * 180 / Math.PI;
            var alt = r - 6371;
            positions.push(t, lon, lat, alt);
        }

        return positions;
    }

    function solveKepler(M, e) {
        var E = M;
        for (var i = 0; i < 15; i++) {
            E = M + e * Math.sin(E);
        }
        return 2 * Math.atan2(
            Math.sqrt(1 + e) * Math.sin(E / 2),
            Math.sqrt(1 - e) * Math.cos(E / 2)
        );
    }

    function updateDashboardFromEntities() {
        var entities = czmlDataSource.entities.values;
        var total = 0, debris = 0, tracking = 0;
        var leo = 0, meo = 0, geo = 0, heo = 0;

        for (var i = 0; i < entities.length; i++) {
            var e = entities[i];
            if (!e.position) continue;
            total++;
            var name = (e.name || '').toUpperCase();
            if (name.indexOf('DEB') !== -1) debris++;
            tracking++;

            try {
                var altProp = e.properties && e.properties.altitude;
                var alt = 0;
                if (altProp) {
                    alt = typeof altProp.getValue === 'function' ? altProp.getValue(Cesium.JulianDate.now()) : altProp;
                }
                if (alt < 2000) leo++;
                else if (alt < 35000) meo++;
                else if (alt >= 35000 && alt <= 36000) geo++;
                else heo++;
            } catch (err) {
                leo++;
            }
        }

        statsData.total = total;
        statsData.debris = debris;
        statsData.tracking = tracking;
        statsData.orbits.leo = leo;
        statsData.orbits.meo = meo;
        statsData.orbits.geo = geo;
        statsData.orbits.heo = heo;

        renderStats();
        generateDemoAlerts();
    }

    function loadStats() {
        fetch(STATS_ENDPOINT)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                statsData.total = data.total_objects || 0;
                statsData.debris = data.debris_count || 0;
                statsData.warnings = data.collision_warnings || 0;
                statsData.tracking = data.satellites || 0;
                statsData.orbits = {
                    leo: (data.orbit_distribution || {}).LEO || 0,
                    meo: (data.orbit_distribution || {}).MEO || 0,
                    geo: (data.orbit_distribution || {}).GEO || 0,
                    heo: (data.orbit_distribution || {}).HEO || 0
                };
                renderStats();
            })
            .catch(function () {
                renderStats();
            });
    }

    function renderStats() {
        animateNumber('stat-total', statsData.total);
        animateNumber('stat-debris', statsData.debris);
        animateNumber('stat-warnings', statsData.warnings);
        animateNumber('stat-tracking', statsData.tracking);

        var orbits = statsData.orbits || {};
        var maxOrbit = Math.max(orbits.leo || 0, orbits.meo || 0, orbits.geo || 0, orbits.heo || 0, 1);

        setBar('bar-leo', 'val-leo', orbits.leo || 0, maxOrbit);
        setBar('bar-meo', 'val-meo', orbits.meo || 0, maxOrbit);
        setBar('bar-geo', 'val-geo', orbits.geo || 0, maxOrbit);
        setBar('bar-heo', 'val-heo', orbits.heo || 0, maxOrbit);
    }

    function setBar(barId, valId, count, max) {
        var pct = max > 0 ? (count / max) * 100 : 0;
        document.getElementById(barId).style.width = pct + '%';
        document.getElementById(valId).textContent = count;
    }

    function animateNumber(elementId, target) {
        var el = document.getElementById(elementId);
        var current = parseInt(el.textContent, 10) || 0;
        if (current === target) return;
        var step = target > current ? 1 : -1;
        var steps = Math.abs(target - current);
        var delay = Math.max(10, Math.min(50, 300 / steps));
        var timer = setInterval(function () {
            current += step;
            el.textContent = current;
            if (current === target) clearInterval(timer);
        }, delay);
    }

    function loadAlerts() {
        fetch(ALERTS_ENDPOINT)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var alerts = [];
                for (var i = 0; i < data.length; i++) {
                    var w = data[i];
                    alerts.push({
                        id: 'alert-' + i,
                        level: w.severity === 'critical' ? 'critical' : (w.severity === 'warning' ? 'caution' : 'warning'),
                        satelliteA: 'NORAD ' + w.satellite1_id,
                        satelliteB: 'NORAD ' + w.satellite2_id,
                        distance: Math.round(w.distance_km * 1000),
                        time: w.time,
                        relativeTime: w.time ? formatRelativeTime(w.time) : '--'
                    });
                }
                statsData.warnings = alerts.filter(function (a) { return a.level === 'critical'; }).length;
                renderAlerts(alerts);
                renderApproaches(alerts.slice(0, 4));
                renderStats();
            })
            .catch(function () {
                // alerts will be generated from demo data
            });
    }

    function formatRelativeTime(isoStr) {
        try {
            var d = new Date(isoStr);
            var now = new Date();
            var diffMs = d - now;
            if (diffMs > 0) {
                var diffMin = Math.round(diffMs / 60000);
                return diffMin + ' 分钟后';
            } else {
                var pastMin = Math.round(-diffMs / 60000);
                return pastMin + ' 分钟前';
            }
        } catch (e) {
            return '--';
        }
    }

    function generateDemoAlerts() {
        var entities = czmlDataSource.entities.values;
        var satNames = [];
        var debNames = [];

        for (var i = 0; i < entities.length; i++) {
            var e = entities[i];
            if (!e.name) continue;
            if (e.name.toUpperCase().indexOf('DEB') !== -1) {
                debNames.push(e.name);
            } else {
                satNames.push(e.name);
            }
        }

        var alerts = [];
        var levels = ['critical', 'caution', 'warning'];
        var distances = [85, 230, 540, 120, 340, 780, 95, 450, 180, 670];

        for (var j = 0; j < Math.min(8, satNames.length); j++) {
            var other = debNames.length > 0
                ? debNames[j % debNames.length]
                : satNames[(j + 3) % satNames.length];
            var dist = distances[j % distances.length];
            var level = dist < 150 ? 'critical' : (dist < 300 ? 'caution' : 'warning');
            var minutesAhead = Math.floor(Math.random() * 180) + 10;
            var alertTime = new Date(Date.now() + minutesAhead * 60000);

            alerts.push({
                id: 'alert-' + j,
                level: level,
                satelliteA: satNames[j],
                satelliteB: other,
                distance: dist,
                time: alertTime.toISOString(),
                relativeTime: minutesAhead + ' 分钟后'
            });
        }

        statsData.warnings = alerts.filter(function (a) { return a.level === 'critical'; }).length;
        renderAlerts(alerts);
        renderApproaches(alerts.slice(0, 4));
        renderStats();
    }

    function renderAlerts(alerts) {
        var container = document.getElementById('collision-alert-list');
        if (!alerts || alerts.length === 0) {
            container.innerHTML = '<div class="empty-placeholder">暂无碰撞预警</div>';
            return;
        }

        alerts.sort(function (a, b) {
            var order = { critical: 0, caution: 1, warning: 2 };
            return (order[a.level] || 2) - (order[b.level] || 2);
        });

        var html = '';
        for (var i = 0; i < alerts.length; i++) {
            var a = alerts[i];
            var timeStr = a.relativeTime || formatAlertTime(a.time);
            html += '<div class="alert-item level-' + a.level + ' new-alert">' +
                '<div class="alert-header">' +
                    '<span class="alert-level ' + a.level + '">' + levelLabel(a.level) + '</span>' +
                    '<span class="alert-time">' + timeStr + '</span>' +
                '</div>' +
                '<div class="alert-body">' +
                    '<span class="highlight">' + escapeHtml(a.satelliteA) + '</span> 与 ' +
                    '<span class="highlight">' + escapeHtml(a.satelliteB) + '</span> 预计接近' +
                '</div>' +
                '<div class="alert-distance">最近距离: ' + a.distance + ' m</div>' +
            '</div>';
        }

        container.innerHTML = html;
    }

    function renderApproaches(alerts) {
        var container = document.getElementById('closest-approaches');
        if (!alerts || alerts.length === 0) {
            container.innerHTML = '<div class="empty-placeholder">暂无接近事件</div>';
            return;
        }

        var html = '';
        for (var i = 0; i < alerts.length; i++) {
            var a = alerts[i];
            var cls = a.distance < 150 ? 'critical' : 'caution';
            html += '<div class="approach-item">' +
                '<div class="approach-header">' +
                    '<span class="approach-pair">' + escapeHtml(a.satelliteA) + ' ↔ ' + escapeHtml(a.satelliteB) + '</span>' +
                    '<span class="approach-distance ' + cls + '">' + a.distance + 'm</span>' +
                '</div>' +
                '<div class="approach-meta">' + a.relativeTime + '</div>' +
            '</div>';
        }

        container.innerHTML = html;
    }

    function levelLabel(level) {
        var map = { critical: '高危', caution: '警告', warning: '注意' };
        return map[level] || level;
    }

    function formatAlertTime(isoStr) {
        try {
            var d = new Date(isoStr);
            return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
        } catch (e) {
            return '--';
        }
    }

    function escapeHtml(text) {
        var div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function updateCurrentTime() {
        var now = new Date();
        var str = now.getFullYear() + '-' +
            pad(now.getMonth() + 1) + '-' +
            pad(now.getDate()) + ' ' +
            pad(now.getHours()) + ':' +
            pad(now.getMinutes()) + ':' +
            pad(now.getSeconds()) + ' UTC+8';
        document.getElementById('current-time').textContent = str;
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
