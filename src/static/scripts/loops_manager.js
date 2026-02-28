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
    document.getElementById('pluginModal').style.display = 'block';
}

function closePluginModal() {
    document.getElementById('pluginModal').style.display = 'none';
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
            location.reload();
        } else {
            showResponseModal('failure', result.error);
        }
    } catch (error) {
        showResponseModal('failure', 'Error: ' + error.message);
    }
});

document.getElementById('pluginForm')?.addEventListener('submit', (e) => {
    e.preventDefault();

    const loopName = document.getElementById('targetLoopName').value;
    const pluginId = document.getElementById('pluginSelect').value;

    if (!pluginId) return;

    // Navigate to full plugin settings page in add mode
    window.location.href = `/plugin/${pluginId}?loop_name=${encodeURIComponent(loopName)}&add_mode=true`;
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
        } else {
            showResponseModal('failure', result.error);
        }
    } catch (error) {
        showResponseModal('failure', 'Error: ' + error.message);
    }
}

// Plugin Actions
async function refreshPluginNow(loopName, pluginId) {
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
        if (!response.ok) {
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
    }
}

// Initialize on page load
window.addEventListener('DOMContentLoaded', () => {
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

// Override Loop Management
async function activateLoopOverride(loopName) {
    try {
        const response = await fetch('/api/override_loop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ loop_name: loopName })
        });
        const result = await response.json();
        if (result.success) {
            showResponseModal('success', `Override active: ${loopName}`);
            location.reload();
        } else {
            showResponseModal('failure', result.error);
        }
    } catch (error) {
        showResponseModal('failure', 'Error activating override: ' + error.message);
    }
}

async function clearOverride() {
    try {
        const response = await fetch('/api/clear_override', { method: 'POST' });
        const result = await response.json();
        if (result.success) {
            showResponseModal('success', 'Schedule resumed');
            location.reload();
        } else {
            showResponseModal('failure', result.error);
        }
    } catch (error) {
        showResponseModal('failure', 'Error clearing override: ' + error.message);
    }
}
