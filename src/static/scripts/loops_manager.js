// Loop Mode Management JavaScript

// Modal Management
function openCreateLoopModal() {
    document.getElementById('modalTitle').textContent = 'Create Loop';
    document.getElementById('oldLoopName').value = '';
    document.getElementById('loopName').value = '';
    document.getElementById('startTime').value = '07:00';
    document.getElementById('endTime').value = '18:00';
    document.getElementById('loopModal').style.display = 'block';
}

function openEditLoopModal(name, startTime, endTime) {
    document.getElementById('modalTitle').textContent = 'Edit Loop';
    document.getElementById('oldLoopName').value = name;
    document.getElementById('loopName').value = name;
    document.getElementById('startTime').value = startTime;
    document.getElementById('endTime').value = endTime;
    document.getElementById('loopModal').style.display = 'block';
}

function closeLoopModal() {
    document.getElementById('loopModal').style.display = 'none';
}

function openAddPluginModal(loopName) {
    document.getElementById('targetLoopName').value = loopName;
    document.getElementById('pluginSelect').value = '';
    document.getElementById('refreshInterval').value = '30';
    document.getElementById('pluginModal').style.display = 'block';
}

function closePluginModal() {
    document.getElementById('pluginModal').style.display = 'none';
}

function openEditPluginModal(loopName, pluginId, refreshIntervalSeconds, pluginSettings) {
    document.getElementById('editLoopName').value = loopName;
    document.getElementById('editPluginId').value = pluginId;
    document.getElementById('editPluginName').textContent = pluginId;

    // Set full settings link with loop context
    document.getElementById('fullSettingsLink').href = `/plugin/${pluginId}?loop_name=${encodeURIComponent(loopName)}&edit_mode=true`;

    // Calculate interval and unit
    let interval, unit;
    if (refreshIntervalSeconds >= 86400 && refreshIntervalSeconds % 86400 === 0) {
        interval = refreshIntervalSeconds / 86400;
        unit = 86400;
    } else if (refreshIntervalSeconds >= 3600 && refreshIntervalSeconds % 3600 === 0) {
        interval = refreshIntervalSeconds / 3600;
        unit = 3600;
    } else if (refreshIntervalSeconds >= 60 && refreshIntervalSeconds % 60 === 0) {
        interval = refreshIntervalSeconds / 60;
        unit = 60;
    } else {
        interval = refreshIntervalSeconds;
        unit = 1;
    }

    document.getElementById('editRefreshInterval').value = interval;
    document.getElementById('editRefreshUnit').value = unit;

    // Show/hide plugin-specific settings
    const clockFaceSettings = document.getElementById('clockFaceSettings');
    const weatherSettings = document.getElementById('weatherSettings');
    const apodSettings = document.getElementById('apodSettings');
    const wikipediaSettings = document.getElementById('wikipediaSettings');
    const aiImageSettings = document.getElementById('aiImageSettings');
    const aiTextSettings = document.getElementById('aiTextSettings');
    const stocksSettings = document.getElementById('stocksSettings');
    const noInlineSettingsMsg = document.getElementById('noInlineSettingsMsg');

    // Hide all first
    clockFaceSettings.style.display = 'none';
    weatherSettings.style.display = 'none';
    apodSettings.style.display = 'none';
    wikipediaSettings.style.display = 'none';
    aiImageSettings.style.display = 'none';
    aiTextSettings.style.display = 'none';
    stocksSettings.style.display = 'none';
    noInlineSettingsMsg.style.display = 'none';

    // Plugins with inline settings
    const pluginsWithInlineSettings = ['clock', 'weather', 'apod', 'wpotd', 'ai_image', 'ai_text', 'stocks'];

    if (pluginId === 'clock') {
        clockFaceSettings.style.display = 'block';

        // Populate clock settings
        document.getElementById('clockFace').value = pluginSettings.selectedClockFace || 'Gradient Clock';
        document.getElementById('primaryColor').value = pluginSettings.primaryColor || '#ffffff';
        document.getElementById('secondaryColor').value = pluginSettings.secondaryColor || '#000000';
    } else if (pluginId === 'weather') {
        weatherSettings.style.display = 'block';

        // Populate weather settings
        document.getElementById('weatherLat').value = pluginSettings.latitude || '';
        document.getElementById('weatherLon').value = pluginSettings.longitude || '';
        document.getElementById('weatherUnits').value = pluginSettings.units || 'imperial';
        document.getElementById('weatherSource').value = pluginSettings.data_source || 'open_meteo';

        // Display options (default to true if not set)
        document.getElementById('displayRefreshTime').checked = pluginSettings.displayRefreshTime !== 'false';
        document.getElementById('displayMetrics').checked = pluginSettings.displayMetrics !== 'false';
        document.getElementById('displayGraph').checked = pluginSettings.displayGraph !== 'false';
        document.getElementById('displayRain').checked = pluginSettings.displayRain === 'true';
        document.getElementById('moonPhase').checked = pluginSettings.moonPhase === 'true';
        document.getElementById('displayForecast').checked = pluginSettings.displayForecast !== 'false';
        document.getElementById('forecastDays').value = pluginSettings.forecastDays || '7';

        document.getElementById('cityResults').innerHTML = '';

        // Show current location if set
        if (pluginSettings.latitude && pluginSettings.longitude) {
            document.getElementById('cityResults').innerHTML =
                `<div style="padding: 8px; background: #e7f3ff; border-radius: 4px; font-size: 14px;">
                    Current: ${pluginSettings.latitude}, ${pluginSettings.longitude}
                </div>`;
        }
    } else if (pluginId === 'apod') {
        apodSettings.style.display = 'block';

        // Populate APOD settings
        document.getElementById('randomizeApod').checked = pluginSettings.randomizeApod === 'true';
        document.getElementById('customDateApod').value = pluginSettings.customDate || '';
        document.getElementById('customDateApod').disabled = document.getElementById('randomizeApod').checked;
        document.getElementById('fitModeApod').value = pluginSettings.fitMode || 'fit';
    } else if (pluginId === 'wpotd') {
        wikipediaSettings.style.display = 'block';

        // Populate Wikipedia settings
        document.getElementById('randomizeWpotd').checked = pluginSettings.randomizeWpotd === 'true';
        document.getElementById('customDate').value = pluginSettings.customDate || '';
        document.getElementById('customDate').disabled = document.getElementById('randomizeWpotd').checked;
        document.getElementById('shrinkToFitWpotd').checked = pluginSettings.shrinkToFitWpotd !== 'false';
        document.getElementById('fitModeWpotd').value = pluginSettings.fitMode || 'fit';
    } else if (pluginId === 'ai_image') {
        aiImageSettings.style.display = 'block';

        // Populate AI Image settings
        document.getElementById('aiImageProvider').value = pluginSettings.provider || 'gemini';
        document.getElementById('aiImagePrompt').value = pluginSettings.textPrompt || '';
        document.getElementById('aiImageRandomize').checked = pluginSettings.randomizePrompt === 'true';
        document.getElementById('aiImageShowTitle').checked = pluginSettings.showTitle !== 'false';
        document.getElementById('aiImageFitMode').value = pluginSettings.fitMode || 'fit';
    } else if (pluginId === 'ai_text') {
        aiTextSettings.style.display = 'block';

        // Populate AI Text settings
        document.getElementById('aiTextProvider').value = pluginSettings.provider || 'gemini';
        document.getElementById('aiTextPrompt').value = pluginSettings.textPrompt || '';
        document.getElementById('aiTextTitle').value = pluginSettings.title || '';
    } else if (pluginId === 'stocks') {
        stocksSettings.style.display = 'block';

        // Populate Stocks settings
        document.getElementById('stocksAutoRefresh').value = pluginSettings.autoRefresh || '0';
    } else {
        // Show message for plugins without inline settings
        noInlineSettingsMsg.style.display = 'block';
    }

    document.getElementById('editPluginModal').style.display = 'block';
}

function closeEditPluginModal() {
    document.getElementById('editPluginModal').style.display = 'none';
}

async function searchCity() {
    const cityName = document.getElementById('citySearch').value.trim();
    const resultsDiv = document.getElementById('cityResults');

    if (!cityName) {
        resultsDiv.innerHTML = '<div style="color: #dc3545; font-size: 14px;">Please enter a city name</div>';
        return;
    }

    resultsDiv.innerHTML = '<div style="font-size: 14px; color: #666;">Searching...</div>';

    try {
        const response = await fetch('/search_city', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ city_name: cityName })
        });

        const result = await response.json();

        if (response.ok && result.cities) {
            let html = '<div style="font-size: 14px; margin-bottom: 5px; font-weight: bold;">Select a location:</div>';
            result.cities.forEach(city => {
                html += `
                    <button type="button"
                            onclick="selectCity(${city.latitude}, ${city.longitude}, '${city.display.replace(/'/g, "\\'")}')"
                            style="display: block; width: 100%; padding: 8px; margin-bottom: 5px; background: #f8f9fa; border: 1px solid #ccc; border-radius: 4px; text-align: left; cursor: pointer;">
                        ${city.display} (${city.country})
                    </button>
                `;
            });
            resultsDiv.innerHTML = html;
        } else {
            resultsDiv.innerHTML = `<div style="color: #dc3545; font-size: 14px;">${result.error || 'No cities found'}</div>`;
        }
    } catch (error) {
        resultsDiv.innerHTML = '<div style="color: #dc3545; font-size: 14px;">Error searching for city</div>';
    }
}

function selectCity(lat, lon, displayName) {
    document.getElementById('weatherLat').value = lat;
    document.getElementById('weatherLon').value = lon;
    document.getElementById('cityResults').innerHTML =
        `<div style="padding: 8px; background: #d4edda; border: 1px solid #c3e6cb; border-radius: 4px; font-size: 14px; color: #155724;">
            ✓ Selected: ${displayName}<br>
            <span style="font-size: 12px;">Lat: ${lat}, Lon: ${lon}</span>
        </div>`;
}

// Form Submissions
document.getElementById('loopForm')?.addEventListener('submit', async (e) => {
    e.preventDefault();

    const oldName = document.getElementById('oldLoopName').value;
    const name = document.getElementById('loopName').value;
    const startTime = document.getElementById('startTime').value;
    const endTime = document.getElementById('endTime').value;

    const endpoint = oldName ? '/update_loop' : '/create_loop';
    const payload = oldName
        ? { old_name: oldName, new_name: name, start_time: startTime, end_time: endTime }
        : { name, start_time: startTime, end_time: endTime };

    try {
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const result = await response.json();
        if (response.ok) {
            sessionStorage.setItem('storedMessage', JSON.stringify({ type: 'success', text: result.message }));
            location.reload();
        } else {
            showResponseModal('failure', result.error);
        }
    } catch (error) {
        showResponseModal('failure', 'Error: ' + error.message);
    }
});

document.getElementById('pluginForm')?.addEventListener('submit', async (e) => {
    e.preventDefault();

    const loopName = document.getElementById('targetLoopName').value;
    const pluginId = document.getElementById('pluginSelect').value;
    const interval = parseInt(document.getElementById('refreshInterval').value);
    const unit = parseInt(document.getElementById('refreshUnit').value);

    const refreshIntervalSeconds = interval * unit;

    try {
        const response = await fetch('/add_plugin_to_loop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                loop_name: loopName,
                plugin_id: pluginId,
                refresh_interval_seconds: refreshIntervalSeconds
            })
        });

        const result = await response.json();
        if (response.ok) {
            sessionStorage.setItem('storedMessage', JSON.stringify({ type: 'success', text: result.message }));
            location.reload();
        } else {
            showResponseModal('failure', result.error);
        }
    } catch (error) {
        showResponseModal('failure', 'Error: ' + error.message);
    }
});

document.getElementById('editPluginForm')?.addEventListener('submit', async (e) => {
    e.preventDefault();

    const loopName = document.getElementById('editLoopName').value;
    const pluginId = document.getElementById('editPluginId').value;
    const interval = parseInt(document.getElementById('editRefreshInterval').value);
    const unit = parseInt(document.getElementById('editRefreshUnit').value);
    const refreshIntervalSeconds = interval * unit;

    // Build plugin settings
    const pluginSettings = {};
    if (pluginId === 'clock') {
        pluginSettings.selectedClockFace = document.getElementById('clockFace').value;
        pluginSettings.primaryColor = document.getElementById('primaryColor').value;
        pluginSettings.secondaryColor = document.getElementById('secondaryColor').value;
    } else if (pluginId === 'weather') {
        const lat = document.getElementById('weatherLat').value;
        const lon = document.getElementById('weatherLon').value;

        if (!lat || !lon) {
            showResponseModal('failure', 'Please search for and select a city location');
            return;
        }

        pluginSettings.latitude = lat;
        pluginSettings.longitude = lon;
        pluginSettings.units = document.getElementById('weatherUnits').value;
        pluginSettings.data_source = document.getElementById('weatherSource').value;

        // Display options
        pluginSettings.displayRefreshTime = document.getElementById('displayRefreshTime').checked ? 'true' : 'false';
        pluginSettings.displayMetrics = document.getElementById('displayMetrics').checked ? 'true' : 'false';
        pluginSettings.displayGraph = document.getElementById('displayGraph').checked ? 'true' : 'false';
        pluginSettings.displayRain = document.getElementById('displayRain').checked ? 'true' : 'false';
        pluginSettings.moonPhase = document.getElementById('moonPhase').checked ? 'true' : 'false';
        pluginSettings.displayForecast = document.getElementById('displayForecast').checked ? 'true' : 'false';
        pluginSettings.forecastDays = parseInt(document.getElementById('forecastDays').value);
    } else if (pluginId === 'apod') {
        pluginSettings.randomizeApod = document.getElementById('randomizeApod').checked ? 'true' : 'false';
        pluginSettings.customDate = document.getElementById('customDateApod').value || '';
        pluginSettings.fitMode = document.getElementById('fitModeApod').value;
    } else if (pluginId === 'wpotd') {
        pluginSettings.randomizeWpotd = document.getElementById('randomizeWpotd').checked ? 'true' : 'false';
        pluginSettings.customDate = document.getElementById('customDate').value || '';
        pluginSettings.shrinkToFitWpotd = document.getElementById('shrinkToFitWpotd').checked ? 'true' : 'false';
        pluginSettings.fitMode = document.getElementById('fitModeWpotd').value;
    } else if (pluginId === 'ai_image') {
        pluginSettings.provider = document.getElementById('aiImageProvider').value;
        pluginSettings.textPrompt = document.getElementById('aiImagePrompt').value;
        pluginSettings.randomizePrompt = document.getElementById('aiImageRandomize').checked ? 'true' : 'false';
        pluginSettings.showTitle = document.getElementById('aiImageShowTitle').checked ? 'true' : 'false';
        pluginSettings.fitMode = document.getElementById('aiImageFitMode').value;
    } else if (pluginId === 'ai_text') {
        pluginSettings.provider = document.getElementById('aiTextProvider').value;
        pluginSettings.textPrompt = document.getElementById('aiTextPrompt').value;
        pluginSettings.title = document.getElementById('aiTextTitle').value || '';
    } else if (pluginId === 'stocks') {
        pluginSettings.autoRefresh = document.getElementById('stocksAutoRefresh').value || '0';
    }

    try {
        const response = await fetch('/update_plugin_settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                loop_name: loopName,
                plugin_id: pluginId,
                refresh_interval_seconds: refreshIntervalSeconds,
                plugin_settings: pluginSettings
            })
        });

        const result = await response.json();
        if (response.ok) {
            sessionStorage.setItem('storedMessage', JSON.stringify({ type: 'success', text: result.message }));
            location.reload();
        } else {
            showResponseModal('failure', result.error);
        }
    } catch (error) {
        showResponseModal('failure', 'Error: ' + error.message);
    }
});

// Loop Actions
async function deleteLoop(loopName) {
    if (!confirm(`Delete loop "${loopName}"?`)) return;

    try {
        const response = await fetch('/delete_loop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ loop_name: loopName })
        });

        const result = await response.json();
        if (response.ok) {
            sessionStorage.setItem('storedMessage', JSON.stringify({ type: 'success', text: result.message }));
            location.reload();
        } else {
            showResponseModal('failure', result.error);
        }
    } catch (error) {
        showResponseModal('failure', 'Error: ' + error.message);
    }
}

// Toggle Randomize
async function toggleRandomize(loopName) {
    try {
        const response = await fetch('/toggle_loop_randomize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ loop_name: loopName })
        });

        const result = await response.json();
        if (response.ok) {
            // Update button appearance
            const btn = document.getElementById(`randomize-${loopName}`);
            if (btn) {
                btn.style.background = result.randomize ? 'var(--success-color)' : 'var(--button-bg)';
                btn.style.color = result.randomize ? 'white' : 'var(--text-primary)';
                btn.textContent = result.randomize ? 'Random' : 'Sequential';
            }
            showResponseModal('success', result.message);
        } else {
            showResponseModal('failure', result.error);
        }
    } catch (error) {
        showResponseModal('failure', 'Error: ' + error.message);
    }
}

// Plugin Actions
async function refreshPluginNow(loopName, pluginId) {
    if (!confirm(`Refresh and display ${pluginId} now?`)) return;

    // Show status bar immediately for instant feedback
    if (window.loopsStatus) {
        window.loopsStatus.showImmediate(pluginId);
    }

    try {
        const response = await fetch('/refresh_plugin_now', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                loop_name: loopName,
                plugin_id: pluginId
            })
        });

        const result = await response.json();
        if (!response.ok) {
            if (window.loopsStatus) window.loopsStatus.hideOnError();
            showResponseModal('failure', result.error || 'Failed to refresh plugin');
        }
        // On success (202), status polling handles the rest
    } catch (error) {
        if (window.loopsStatus) window.loopsStatus.hideOnError();
        showResponseModal('failure', 'Error: ' + error.message);
    }
}

async function removePluginFromLoop(loopName, pluginId) {
    try {
        const response = await fetch('/remove_plugin_from_loop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                loop_name: loopName,
                plugin_id: pluginId
            })
        });

        const result = await response.json();
        if (response.ok) {
            sessionStorage.setItem('storedMessage', JSON.stringify({ type: 'success', text: result.message }));
            location.reload();
        } else {
            showResponseModal('failure', result.error);
        }
    } catch (error) {
        showResponseModal('failure', 'Error: ' + error.message);
    }
}

// Rotation Interval
async function saveRotationInterval() {
    const interval = document.getElementById('rotation-interval').value;
    const unit = document.getElementById('rotation-unit').value;

    try {
        const response = await fetch('/update_rotation_interval', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ interval, unit })
        });

        const result = await response.json();
        if (response.ok) {
            showResponseModal('success', result.message);
        } else {
            showResponseModal('failure', result.error);
        }
    } catch (error) {
        showResponseModal('failure', 'Error: ' + error.message);
    }
}

// Close modals when clicking outside
window.onclick = function(event) {
    if (event.target.id === 'loopModal') {
        closeLoopModal();
    } else if (event.target.id === 'pluginModal') {
        closePluginModal();
    } else if (event.target.id === 'editPluginModal') {
        closeEditPluginModal();
    }
}

// Show stored message on page load
window.addEventListener('DOMContentLoaded', () => {
    const storedMessage = sessionStorage.getItem('storedMessage');
    if (storedMessage) {
        const { type, text } = JSON.parse(storedMessage);
        showResponseModal(type, text);
        sessionStorage.removeItem('storedMessage');
    }

    // Edit plugin buttons navigate to full settings page
    document.querySelectorAll('.edit-plugin-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const loopName = this.dataset.loopName;
            const pluginId = this.dataset.pluginId;
            window.location.href = `/plugin/${pluginId}?loop_name=${encodeURIComponent(loopName)}&edit_mode=true`;
        });
    });

    // Setup drag-and-drop for plugin reordering
    setupPluginDragAndDrop();

    // Wikipedia randomize date toggle
    const randomizeCheckbox = document.getElementById('randomizeWpotd');
    if (randomizeCheckbox) {
        randomizeCheckbox.addEventListener('change', function() {
            document.getElementById('customDate').disabled = this.checked;
        });
    }

    // APOD randomize date toggle
    const randomizeApodCheckbox = document.getElementById('randomizeApod');
    if (randomizeApodCheckbox) {
        randomizeApodCheckbox.addEventListener('change', function() {
            document.getElementById('customDateApod').disabled = this.checked;
        });
    }
});

// Drag and Drop functionality
function setupPluginDragAndDrop() {
    let draggedItem = null;

    document.querySelectorAll('.plugin-ref-item').forEach(item => {
        item.addEventListener('dragstart', function(e) {
            draggedItem = this;
            this.style.opacity = '0.5';
            e.dataTransfer.effectAllowed = 'move';
        });

        item.addEventListener('dragend', function() {
            this.style.opacity = '1';
            document.querySelectorAll('.plugin-ref-item').forEach(el => {
                el.classList.remove('drag-over');
            });
        });

        item.addEventListener('dragover', function(e) {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            if (this !== draggedItem) {
                this.classList.add('drag-over');
                this.style.borderTop = '2px solid #007bff';
            }
        });

        item.addEventListener('dragleave', function() {
            this.classList.remove('drag-over');
            this.style.borderTop = '';
        });

        item.addEventListener('drop', function(e) {
            e.preventDefault();
            this.style.borderTop = '';

            if (this !== draggedItem && draggedItem) {
                const pluginList = this.parentElement;
                const loopName = pluginList.dataset.loopName;
                const allItems = Array.from(pluginList.children);
                const draggedIndex = allItems.indexOf(draggedItem);
                const targetIndex = allItems.indexOf(this);

                // Reorder in DOM
                if (draggedIndex < targetIndex) {
                    this.parentNode.insertBefore(draggedItem, this.nextSibling);
                } else {
                    this.parentNode.insertBefore(draggedItem, this);
                }

                // Save new order to server
                savePluginOrder(loopName, pluginList);
            }
        });
    });
}

async function savePluginOrder(loopName, pluginList) {
    const pluginIds = Array.from(pluginList.children).map(item => item.dataset.pluginId);

    try {
        const response = await fetch('/reorder_plugins', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                loop_name: loopName,
                plugin_ids: pluginIds
            })
        });

        const result = await response.json();
        if (response.ok) {
            // Success - could show a subtle notification
            console.log('Plugin order saved');
        } else {
            showResponseModal('failure', result.error);
            location.reload(); // Reload to restore correct order
        }
    } catch (error) {
        showResponseModal('failure', 'Error saving order: ' + error.message);
        location.reload();
    }
}
