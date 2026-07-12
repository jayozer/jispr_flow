"""Native AppKit Settings window. Imported only by ``local-flow settings``."""

from __future__ import annotations

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSButton,
    NSComboBox,
    NSControlStateValueOn,
    NSFont,
    NSPopUpButton,
    NSScrollView,
    NSSwitchButton,
    NSTextField,
    NSTextView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSMakeRect, NSObject

from local_flow.errors import LocalFlowError
from local_flow.settings.controller import ASR_PRESETS, SettingsController

_WINDOW_CONTROLLER = None


def _label(text: str, frame, *, bold: bool = False):
    field = NSTextField.labelWithString_(text)
    field.setFrame_(frame)
    if bold:
        field.setFont_(NSFont.boldSystemFontOfSize_(13))
    return field


def _text(value: str, frame):
    field = NSTextField.alloc().initWithFrame_(frame)
    field.setStringValue_(value)
    return field


def _popup(items: list[str], frame):
    popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(frame, False)
    popup.addItemsWithTitles_(items)
    return popup


def _combo(items: list[str], frame):
    combo = NSComboBox.alloc().initWithFrame_(frame)
    combo.addItemsWithObjectValues_(items)
    return combo


def _button(title: str, frame, target, action: str):
    button = NSButton.alloc().initWithFrame_(frame)
    button.setTitle_(title)
    button.setTarget_(target)
    button.setAction_(action)
    return button


def _is_environment_source(source: str) -> bool:
    return source == "environment" or source.endswith(":environment")


class SettingsWindowController(NSObject):
    def initWithController_(self, controller):
        self = objc.super(SettingsWindowController, self).init()
        if self is None:
            return None
        self.controller = controller
        self.view_model = controller.load()
        self.original_config = self.view_model.snapshot.config
        self.controls = {}
        self._build_window()
        self._populate()
        return self

    @objc.python_method
    def _add(self, view):
        self.window.contentView().addSubview_(view)
        return view

    @objc.python_method
    def _row(self, y, title, field_name, control):
        self._add(_label(title, NSMakeRect(24, y + 3, 155, 22)))
        self._add(control)
        source = self.view_model.snapshot.sources[field_name]
        if _is_environment_source(source):
            control.setEnabled_(False)
        self.controls[field_name] = control

    @objc.python_method
    def _build_window(self):
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 710, 830), style, NSBackingStoreBuffered, False
        )
        self.window.setTitle_("JiSpr Settings & Personalization")
        self.window.center()
        self.window.setDelegate_(self)

        y = 788
        self._add(_label("Models", NSMakeRect(24, y, 200, 24), bold=True))
        y -= 34
        self.preset = _popup(list(ASR_PRESETS), NSMakeRect(180, y, 505, 28))
        self.preset.setTarget_(self)
        self.preset.setAction_("presetChanged:")
        self._add(_label("Preset", NSMakeRect(24, y + 3, 155, 22)))
        self._add(self.preset)

        y -= 34
        self._row(
            y,
            "ASR backend",
            "asr_backend",
            _popup(
                ["mlx-parakeet", "mlx-whisper", "faster-whisper"],
                NSMakeRect(180, y, 505, 28),
            ),
        )
        y -= 34
        self._row(y, "ASR model", "asr_model", _text("", NSMakeRect(180, y, 505, 24)))
        y -= 34
        self._row(
            y,
            "Language",
            "asr_language",
            _combo(["auto", "en", "es", "fr", "de"], NSMakeRect(180, y, 505, 26)),
        )
        y -= 34
        self._row(
            y,
            "ASR device",
            "asr_device",
            _popup(["auto", "cpu", "cuda"], NSMakeRect(180, y, 505, 28)),
        )
        y -= 34
        self._row(
            y,
            "Compute type",
            "asr_compute_type",
            _popup(["int8", "float16", "float32"], NSMakeRect(180, y, 505, 28)),
        )
        y -= 34
        self._row(
            y,
            "Polish",
            "polish_backend",
            _popup(["lmstudio", "rules"], NSMakeRect(180, y, 505, 28)),
        )
        y -= 34
        self.lm_model = NSComboBox.alloc().initWithFrame_(NSMakeRect(180, y, 400, 26))
        self._row(y, "LM Studio model", "lmstudio_model", self.lm_model)
        refresh = _button("Refresh", NSMakeRect(590, y, 95, 26), self, "refreshModels:")
        self._add(refresh)
        y -= 30
        self.model_status = self._add(_label("Not checked", NSMakeRect(180, y, 500, 22)))

        y -= 34
        self._add(_label("Polish & Appearance", NSMakeRect(24, y, 240, 24), bold=True))
        y -= 34
        self._row(
            y,
            "Cleanup level",
            "cleanup_level",
            _popup(["none", "light", "medium", "high"], NSMakeRect(180, y, 505, 28)),
        )
        y -= 34
        self.style_popup = _popup(self.view_model.styles, NSMakeRect(180, y, 505, 28))
        self._row(y, "Writing style", "style", self.style_popup)
        y -= 34
        pill = NSButton.alloc().initWithFrame_(NSMakeRect(180, y, 505, 24))
        pill.setButtonType_(NSSwitchButton)
        pill.setTitle_("Show floating recording pill")
        self._row(y, "Recording pill", "floating_pill", pill)
        y -= 34
        self._row(
            y,
            "Pill style",
            "pill_style",
            _popup(["compact", "expanded"], NSMakeRect(180, y, 505, 28)),
        )
        y -= 68
        source = self.view_model.snapshot.sources["lmstudio_system_prompt"]
        self._add(_label("Additional prompt", NSMakeRect(24, y + 40, 155, 22)))
        prompt_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(180, y, 505, 66))
        prompt_scroll.setHasVerticalScroller_(True)
        self.prompt = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 485, 66))
        prompt_scroll.setDocumentView_(self.prompt)
        self._add(prompt_scroll)
        self.prompt.setEditable_(not _is_environment_source(source))

        y -= 38
        self._add(_label("Dictionary", NSMakeRect(24, y, 200, 24), bold=True))
        self.dictionary_popup = _popup([], NSMakeRect(180, y - 2, 250, 28))
        self.dictionary_popup.setTarget_(self)
        self.dictionary_popup.setAction_("dictionarySelected:")
        self._add(self.dictionary_popup)
        self.dictionary_term = self._add(_text("", NSMakeRect(440, y, 245, 24)))
        self.dictionary_term.setPlaceholderString_("Edit selected term")
        y -= 32
        self.dictionary_starred = NSButton.alloc().initWithFrame_(NSMakeRect(180, y, 100, 24))
        self.dictionary_starred.setButtonType_(NSSwitchButton)
        self.dictionary_starred.setTitle_("Starred")
        self._add(self.dictionary_starred)
        self._add(_button("Add", NSMakeRect(355, y, 80, 26), self, "addDictionary:"))
        self._add(_button("Update", NSMakeRect(440, y, 80, 26), self, "updateDictionary:"))
        self._add(_button("Remove", NSMakeRect(525, y, 80, 26), self, "removeDictionary:"))

        y -= 38
        self._add(_label("Correction aliases", NSMakeRect(24, y, 200, 24), bold=True))
        self.alias_popup = _popup([], NSMakeRect(180, y - 2, 250, 28))
        self.alias_popup.setTarget_(self)
        self.alias_popup.setAction_("aliasSelected:")
        self._add(self.alias_popup)
        self.alias_trigger = self._add(_text("", NSMakeRect(440, y, 245, 24)))
        self.alias_trigger.setPlaceholderString_("Trigger")
        y -= 32
        self.alias_expansion = self._add(_text("", NSMakeRect(180, y, 245, 24)))
        self.alias_expansion.setPlaceholderString_("Expansion")
        self._add(_button("Add", NSMakeRect(440, y, 70, 26), self, "addAlias:"))
        self._add(_button("Update", NSMakeRect(515, y, 80, 26), self, "updateAlias:"))
        self._add(_button("Remove", NSMakeRect(600, y, 80, 26), self, "removeAlias:"))

        y -= 44
        self.status = self._add(_label("", NSMakeRect(24, y + 2, 510, 24)))
        save = _button("Save Settings", NSMakeRect(545, y, 140, 30), self, "save:")
        save.setKeyEquivalent_("\r")
        self._add(save)

    @objc.python_method
    def _select(self, control, value):
        control.selectItemWithTitle_(str(value))

    @objc.python_method
    def _populate(self):
        config = self.view_model.snapshot.config
        preset_fields = ("asr_backend", "asr_model")
        preset_locked = any(
            _is_environment_source(self.view_model.snapshot.sources[field])
            for field in preset_fields
        )
        self._select(
            self.preset,
            "Custom" if preset_locked else self.controller.matching_preset(config),
        )
        self.preset.setEnabled_(not preset_locked)
        self._select(self.controls["asr_backend"], config.asr_backend)
        self.controls["asr_model"].setStringValue_(config.asr_model)
        self.controls["asr_language"].setStringValue_(config.asr_language)
        self._select(self.controls["asr_device"], config.asr_device)
        self._select(self.controls["asr_compute_type"], config.asr_compute_type)
        self._select(self.controls["polish_backend"], config.polish_backend)
        self.lm_model.setStringValue_(config.lmstudio_model)
        self._select(self.controls["cleanup_level"], config.cleanup_level)
        self._select(self.controls["style"], config.style)
        self.controls["floating_pill"].setState_(
            NSControlStateValueOn if config.floating_pill else 0
        )
        self._select(self.controls["pill_style"], config.pill_style)
        self.prompt.setString_(config.lmstudio_system_prompt)
        self._reload_personalization()

    @objc.python_method
    def _reload_personalization(self):
        self.view_model = self.controller.load()
        self.dictionary_popup.removeAllItems()
        self.dictionary_popup.addItemsWithTitles_(
            [entry["term"] for entry in self.view_model.dictionary_entries]
        )
        self.alias_popup.removeAllItems()
        self.alias_popup.addItemsWithTitles_(list(self.view_model.aliases))
        if self.view_model.dictionary_entries:
            self.dictionary_popup.selectItemAtIndex_(0)
            self.dictionarySelected_(self.dictionary_popup)
        else:
            self.dictionary_term.setStringValue_("")
            self.dictionary_starred.setState_(0)
        if self.view_model.aliases:
            self.alias_popup.selectItemAtIndex_(0)
            self.aliasSelected_(self.alias_popup)
        else:
            self.alias_trigger.setStringValue_("")
            self.alias_expansion.setStringValue_("")

    def presetChanged_(self, _sender):
        values = self.controller.preset(self.preset.titleOfSelectedItem())
        for name, value in values.items():
            control = self.controls.get(name)
            if control is None or not control.isEnabled():
                continue
            if isinstance(control, NSPopUpButton):
                self._select(control, value)
            elif name == "lmstudio_model":
                control.setStringValue_(str(value))
            else:
                control.setStringValue_(str(value))

    def refreshModels_(self, _sender):
        models, status = self.controller.refresh_models()
        if models:
            current = self.lm_model.stringValue()
            self.lm_model.removeAllItems()
            self.lm_model.addItemsWithObjectValues_(models)
            self.lm_model.setStringValue_(current or models[0])
        self.model_status.setStringValue_(status)

    def save_(self, _sender):
        backend = self.controls["asr_backend"].titleOfSelectedItem()
        model = self.controls["asr_model"].stringValue()
        profile = self.original_config.asr_profile
        if backend != self.original_config.asr_backend or model != self.original_config.asr_model:
            profile = "custom"
        changes = {
            "asr_profile": profile,
            "asr_backend": backend,
            "asr_model": model,
            "asr_language": self.controls["asr_language"].stringValue(),
            "asr_device": self.controls["asr_device"].titleOfSelectedItem(),
            "asr_compute_type": self.controls["asr_compute_type"].titleOfSelectedItem(),
            "polish_backend": self.controls["polish_backend"].titleOfSelectedItem(),
            "lmstudio_model": self.lm_model.stringValue(),
            "cleanup_level": self.controls["cleanup_level"].titleOfSelectedItem(),
            "style": self.controls["style"].titleOfSelectedItem(),
            "floating_pill": self.controls["floating_pill"].state()
            == NSControlStateValueOn,
            "pill_style": self.controls["pill_style"].titleOfSelectedItem(),
            "lmstudio_system_prompt": self.prompt.string(),
        }
        try:
            self.view_model = self.controller.save(changes)
        except LocalFlowError as exc:
            self.status.setStringValue_(f"Could not save: {exc.message}")
            return
        self.original_config = self.view_model.snapshot.config
        self.status.setStringValue_("Saved. Restart JiSpr for configuration changes.")

    def dictionarySelected_(self, _sender):
        selected = self.dictionary_popup.titleOfSelectedItem()
        if not selected:
            return
        entry = next(
            item for item in self.view_model.dictionary_entries if item["term"] == selected
        )
        self.dictionary_term.setStringValue_(entry["term"])
        self.dictionary_starred.setState_(
            NSControlStateValueOn if entry.get("starred", False) else 0
        )

    def addDictionary_(self, _sender):
        if self.controller.add_dictionary(self.dictionary_term.stringValue()):
            self._reload_personalization()
            self.status.setStringValue_("Dictionary updated for the next utterance.")

    def updateDictionary_(self, _sender):
        original = self.dictionary_popup.titleOfSelectedItem()
        if original and self.controller.update_dictionary(
            original,
            self.dictionary_term.stringValue(),
            starred=self.dictionary_starred.state() == NSControlStateValueOn,
        ):
            self._reload_personalization()
            self.status.setStringValue_("Dictionary updated for the next utterance.")

    def removeDictionary_(self, _sender):
        selected = self.dictionary_popup.titleOfSelectedItem()
        if selected and self.controller.remove_dictionary(selected):
            self._reload_personalization()
            self.status.setStringValue_("Dictionary term removed.")

    def aliasSelected_(self, _sender):
        selected = self.alias_popup.titleOfSelectedItem()
        if selected:
            self.alias_trigger.setStringValue_(selected)
            self.alias_expansion.setStringValue_(self.view_model.aliases[selected])

    def addAlias_(self, _sender):
        trigger = self.alias_trigger.stringValue().strip()
        if trigger:
            self.controller.set_alias(trigger, self.alias_expansion.stringValue())
            self._reload_personalization()
            self.status.setStringValue_("Correction alias applies to the next utterance.")

    def updateAlias_(self, _sender):
        original = self.alias_popup.titleOfSelectedItem()
        if original and self.controller.update_alias(
            original, self.alias_trigger.stringValue(), self.alias_expansion.stringValue()
        ):
            self._reload_personalization()
            self.status.setStringValue_("Correction alias updated.")

    def removeAlias_(self, _sender):
        selected = self.alias_popup.titleOfSelectedItem()
        if selected and self.controller.remove_alias(selected):
            self._reload_personalization()
            self.status.setStringValue_("Correction alias removed.")

    def windowWillClose_(self, _notification):
        NSApplication.sharedApplication().terminate_(self)


def run_settings() -> None:
    global _WINDOW_CONTROLLER

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    window_controller = SettingsWindowController.alloc().initWithController_(
        SettingsController()
    )
    window_controller.window.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)
    # Keep the controller alive for the duration of the AppKit event loop.
    _WINDOW_CONTROLLER = window_controller
    app.run()
