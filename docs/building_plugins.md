# Building DashPi Plugins

This guide walks you through the process of creating a new plugin for DashPi.

### 1. Create a Directory for Your Plugin

- Navigate to the `src/plugins` directory.
- Create a new directory named after your plugin. The directory name will be the `id` of your plugin and should be all lowercase with no spaces. Example:

  ```bash
  mkdir plugins/clock
  ```

### 2. Create a Python File and Class for the Plugin

- Inside your new plugin directory, create a Python file with the same name as the directory.
- Define a class in the file that inherits from `BasePlugin`.
- In your new class, implement the `generate_image` function
    - Arguments:
        - `settings`: A dictionary of plugin configuration values from the form inputs in the web UI.
        - `device_config`: An instance of the Config class, used to retrieve device configurations such as display resolution or dotenv keys for any secrets.
    - Return a single `PIL.Image` object to be displayed.
    - Plugins should generate images using relative sizing (percentages of display dimensions) rather than hardcoded pixel values. This ensures they render correctly across all display types — from 800×480 e-ink to 1024×600 LCD.
    - If there are any issues (e.g., missing configuration options or API keys), raise a `RuntimeError` exception with a clear and concise message to be displayed in the web UI.
- (Optional) Override `cleanup(self, settings)` if your plugin stores files or external resources that should be cleaned up when a plugin instance is deleted from a loop.
- (Optional) If your settings template requires any additional variables, override the default `generate_settings_template` function
    - In this function, call `BasePlugin`'s `generate_settings_template` method to retrieve the default template parameters. Add any extra key-value pairs needed for your template and return the updated dictionary.
    - To add the predefined style settings (see the Weather and AI Text plugins in the Web UI) to your plugin settings page, set `style_settings` to True.
    - Example:
        ```python
        def generate_settings_template(self):
            template_params = super().generate_settings_template()
            template_params['custom_template_variable'] = self.get_custom_variable()
            template_params['style_settings'] = True
            return template_params
        ```
- (Optional) If your plugin needs to cache or store data across refreshes, you can manage this within the `generate_image` function.
    - For example, you can retrieve and update values as follows:
        ```python
        def generate_image(self, settings, device_config):
            # retrieve stored value with a default
            cached_index = settings.get("index", 0)

            # update value for next refresh
            settings["index"] = settings["index"] + 1
        ```

### 3. Create a Settings Template (Optional)

If your plugin requires user configuration through the web UI, you'll need to define a settings template.
- In your plugin directory, create a `settings.html` file
- Inside this file, define HTML input elements for any settings required by your plugin:
    - The `name` attribute of each input element will be passed as keys in the `settings` argument of the `generate_image` function
- Any template variables added in `generate_settings_template` function will be accessible in the settings template. This is useful for dynamic content, such as populating options in a dropdown menu.
- Ensure the settings template visually matches the style of the existing web UI and other plugin templates for consistency.
- When a plugin is added to a loop, editing the plugin instance should prepopulate the form with the current settings, and saving changes should update the settings accordingly.

### 4. Add an Icon for Your Plugin

- Create an `icon.png` file in your plugin's directory. This will be the icon displayed in the web UI.
    - Ensure the icon visually matches the style of existing icons in the project.

### 5. Register Your Plugin

- Create a `plugin-info.json` in your plugin folder
- Add an object for your plugin using the following structure:
    ```json
    {
        "display_name": "Clock",
        "id": "clock",
        "class": "Clock",
        "repository": ""
    }
    ```
    - `display_name`: The name shown in the web UI for the plugin.
    - `id`: A unique identifier for the plugin (use lowercase and avoid spaces).
    - `class`: The name of your plugin's Python class.
    - `repository`: GitHub Repository URL, if the plugin will be published as a third party plugin.
- Plugins will be loaded on startup if the folder contains a `plugin-info.json`

## Display Compatibility

DashPi supports multiple display types. Plugins should be display-agnostic:

- **Do not hardcode pixel dimensions.** Use `device_config.get_config("resolution")` to get the display size and scale your layout proportionally.
- **All 24 built-in plugins** work across LCD, Inky e-paper, and Waveshare e-paper displays without modification.
- Plugins can check display capabilities via the `device_config` if needed, but this should rarely be necessary.

## Test Your Plugin

- Restart the DashPi service by running
    ```bash
    sudo systemctl restart dashpi.service
    ```
- Test and ensure that your plugin:
    - Loads correctly on service start.
    - Appears under the "Plugins" section in the web UI with its icon.
    - Generates images for different display sizes and orientations.
    - Settings template is rendered correctly.
    - Generates and displays images with immediate updates and in a loop.
    - Settings template is prepopulated and saved correctly when editing an existing loop entry.

## Example Directory Structure

Here's how your plugin directory should look:

```
plugins/{plugin_id}/
    ├── {plugin_id}.py          # Main plugin script
    ├── plugin-info.json        # Plugin manifest file
    ├── icon.png                # Plugin icon
    ├── settings.html           # Optional: Plugin settings page (if applicable)
    ├── resources/              # Optional: Static resources (images, data files, etc.)
    └── {other files}           # Any additional files used by the plugin
```

## Prepopulating Forms for Plugin Instances

When a plugin is added to a loop, a "Plugin Instance" is created, and its settings are stored in the `src/config/device.json` file. These settings can be updated from the loop page, so the form in settings.html should be prepopulated with the existing settings.

- The `loadPluginSettings` variable should be checked to ensure the settings page is in "edit" mode.
- Plugin settings are accessible via the `pluginSettings` object.
- Example:
    ```JavaScript
    document.addEventListener('DOMContentLoaded', () => {
        if (loadPluginSettings) {
            // Text Input
            document.getElementById('{textInputElementId}').value = pluginSettings.textInputElementName || '';

            // Radio
            document.querySelector(`input[name="radioElementName"][value="${pluginSettings.radioElementName}"]`).checked = true;

            // Color Input
            document.getElementById('{colorInputElementId}').value = pluginSettings.colorInputElementName

            ...
        }
    });
    ```

## Publishing a Third Party Plugin

To publish your plugin as a third party plugin for others to install, you'll need to create a new repository. See [Creating a new repository](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository) in the GitHub documentation.

Note: It's recommended to name your repository `DashPi-{plugin_name}` so it's easy to discover via GitHub search.

### Repository Structure

Your repository must include:

- **A folder named after your `plugin_id`**
  - This folder will contain your plugin source code, see [example directory structure](./building_plugins.md#example-directory-structure) for the contents of the folder.
  - When a user installs your plugin, this folder is copied into the `src/plugins/` directory.

- **A README file** containing:
  - A short, clear, one-sentence description of what the plugin does.
  - At least one high-quality screenshot showing the plugin running.
  - Any external APIs the plugin depends on, including:
    - Links to the API documentation.
    - Instructions for obtaining and configuring API keys, if required.
    - Whether the API requires a key and any known usage limits or costs (for example, free tiers or rate limits).
  - The current development status (for example: actively maintained, work in progress, looking for a maintainer, or no longer maintained).

---

See [DashPi-Plugin-Template](https://github.com/SHagler2/DashPi-Plugin-Template) for a sample template of a third party plugin.

Once you're done, feel free to add your plugin to the [3rd Party Plugin List](https://github.com/SHagler2/DashPi/wiki/3rd-Party-Plugins) and share it in the [🙌 Show and Tell Discussion Board](https://github.com/SHagler2/DashPi/discussions/categories/show-and-tell).
