
let currentLang = "en";

if (document.getElementById('map')) {
var map = L.map('map',{
    dragging: false,
    zoomControl: false,
    scrollWheelZoom: false,
    doubleClickZoom: false,
    //  boxZoom: false,
    keyboard: false,
    //  tap: false,
    touchZoom: false}
).setView([39.5, -8], 7);



var stats = {
    "Lisboa": "Turnout: 68%",
    "Porto": "Turnout: 64%"
};

fetch("portugal_district.geojson")
    .then(res => res.json())
    .then(data => {

        function style(feature) {
            return {
                color: "#333",
                weight: 1,
                fillColor: "#66cc66",
                fillOpacity: 0.6
            };
        }

var selectedLayer = null;

function onEachFeature(feature, layer) {
    layer.on({
        mouseover: function (e) {
            var name = feature.properties.name;
            var info = stats[name];
            layer.bindTooltip(name + "<br>" + info).openTooltip();
            layer.setStyle({ fillColor: "#15a821" });
        },

        mouseout: function (e) {
            geojson.resetStyle(layer);
            if (selectedLayer === layer) {
                layer.setStyle({ fillColor: "#15a821" ,color: "#356040", weight: 3}); // re-apply highlight if still selected
            }
        },

        click: function (e) {
            if (selectedLayer) {
                geojson.resetStyle(selectedLayer); // reset previous
            }
            selectedLayer = layer;
            layer.setStyle({
                color: "#356040",  // red border
                weight: 3,         // thicker border
                fillColor: "#15a821"
            });
            layer.bringToFront();
        }
    });
}
        var geojson = L.geoJSON(data, {
            style: style,
            onEachFeature: onEachFeature
        }).addTo(map);

    });
}





function toggleLanguage() {
    currentLang = currentLang === "en" ? "pt" : "en";

    const elements = document.querySelectorAll("[data-en]");
    elements.forEach(el => {
        el.textContent = el.getAttribute(`data-${currentLang}`);
    });

    updateButton();
}

function updateButton() {
    const btn = document.querySelector(".nav-right button");
    if (!btn) return;
    btn.innerHTML = currentLang === "en" 
        ? '<img src="images/icon_portugal.png" alt="PT">' 
        : '<img src="images/icon_uk.png" alt="EN">';
}

updateButton();