from __future__ import annotations

import threading
import tkinter as tk
from dataclasses import replace
from tkinter import messagebox, scrolledtext, ttk

from x_sidechain.config import AppConfig, ConfigStore, PROVIDER_DEFAULTS, provider_status
from x_sidechain.models import AgentSpec, DebateResult
from x_sidechain.orchestrator import SidechainOrchestrator
from x_sidechain.prompts import EXPLORER_ROLE, VALIDATOR_ROLE
from x_sidechain.providers import create_provider


BG = "#0b0f14"
PANEL = "#121922"
PANEL_ALT = "#17212d"
TEXT = "#dce7f3"
MUTED = "#8fa3b8"
ACCENT = "#3dd6a3"
WARNING = "#ffca5c"


def _transcript(result: DebateResult) -> str:
    sections = [
        ("AGENT A — INDEPENDENT", result.initial_a.text),
        ("AGENT B — INDEPENDENT", result.initial_b.text),
        ("AGENT A — CRITIQUE", result.critique_a.text),
        ("AGENT B — CRITIQUE", result.critique_b.text),
    ]
    return "\n\n".join(f"{title}\n{'=' * len(title)}\n{text}" for title, text in sections)


class SidechainApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("X-SIDECHAIN")
        self.geometry("1180x820")
        self.minsize(900, 650)
        self.configure(bg=BG)
        self.store = ConfigStore()
        self.config_data = self.store.load()
        self._build_style()
        self._build_ui()
        self._refresh_provider_state()

    def _build_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Sans", 10))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("Title.TLabel", background=BG, foreground=ACCENT, font=("Sans", 20, "bold"))
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
        style.configure("TButton", padding=(14, 8), font=("Sans", 10, "bold"))
        style.configure("Accent.TButton", background=ACCENT, foreground="#07110e")
        style.map("Accent.TButton", background=[("active", "#75e8c1"), ("disabled", "#31594c")])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(14, 8))
        style.map("TNotebook.Tab", background=[("selected", PANEL_ALT)], foreground=[("selected", TEXT)])
        style.configure("TCombobox", fieldbackground=PANEL_ALT, foreground=TEXT)
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL_ALT)

    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=(22, 18))
        header.pack(fill="x")
        ttk.Label(header, text="X-SIDECHAIN", style="Title.TLabel").pack(side="left")
        ttk.Label(
            header,
            text="independent reasoning · cross-examination · evidence gate",
            style="Muted.TLabel",
        ).pack(side="left", padx=(18, 0), pady=(7, 0))

        settings = ttk.Frame(self, style="Panel.TFrame", padding=16)
        settings.pack(fill="x", padx=22, pady=(0, 14))
        self.provider_a = tk.StringVar(value=self.config_data.agent_a_provider)
        self.model_a = tk.StringVar(value=self.config_data.agent_a_model)
        self.provider_b = tk.StringVar(value=self.config_data.agent_b_provider)
        self.model_b = tk.StringVar(value=self.config_data.agent_b_model)
        self.synthesizer = tk.StringVar(value=self.config_data.synthesizer)

        self._agent_controls(settings, 0, "Agent A · Discovery", self.provider_a, self.model_a)
        self._agent_controls(settings, 3, "Agent B · Validation", self.provider_b, self.model_b)
        ttk.Label(settings, text="Final synthesis", style="Panel.TLabel").grid(row=0, column=6, sticky="w")
        ttk.Combobox(
            settings,
            textvariable=self.synthesizer,
            values=("a", "b"),
            width=8,
            state="readonly",
        ).grid(row=1, column=6, padx=(0, 12), sticky="w")
        self.key_state = ttk.Label(settings, text="", style="Panel.TLabel")
        self.key_state.grid(row=1, column=7, sticky="w")
        for column in (2, 5, 7):
            settings.columnconfigure(column, weight=1)

        body = ttk.Panedwindow(self, orient="vertical")
        body.pack(fill="both", expand=True, padx=22, pady=(0, 14))
        prompt_panel = ttk.Frame(body, style="Panel.TFrame", padding=14)
        output_panel = ttk.Frame(body, style="Panel.TFrame", padding=8)
        body.add(prompt_panel, weight=2)
        body.add(output_panel, weight=5)

        ttk.Label(prompt_panel, text="Task / security claim", style="Panel.TLabel").pack(anchor="w")
        self.prompt = scrolledtext.ScrolledText(
            prompt_panel,
            height=8,
            wrap="word",
            bg=PANEL_ALT,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            padx=12,
            pady=12,
            font=("Monospace", 10),
        )
        self.prompt.pack(fill="both", expand=True, pady=(8, 0))

        self.tabs = ttk.Notebook(output_panel)
        self.tabs.pack(fill="both", expand=True)
        self.final_output = self._output_tab("Joint decision")
        self.transcript_output = self._output_tab("Full transcript")
        self.audit_output = self._output_tab("Audit")

        footer = ttk.Frame(self, padding=(22, 0, 22, 18))
        footer.pack(fill="x")
        self.status = tk.StringVar(value="Ready")
        ttk.Label(footer, textvariable=self.status, style="Muted.TLabel").pack(side="left")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=180)
        self.progress.pack(side="right", padx=(12, 0))
        self.run_button = ttk.Button(footer, text="Run sidechain", style="Accent.TButton", command=self._run)
        self.run_button.pack(side="right")

    def _agent_controls(
        self,
        parent: ttk.Frame,
        column: int,
        title: str,
        provider_var: tk.StringVar,
        model_var: tk.StringVar,
    ) -> None:
        ttk.Label(parent, text=title, style="Panel.TLabel").grid(row=0, column=column, columnspan=2, sticky="w")
        provider = ttk.Combobox(
            parent,
            textvariable=provider_var,
            values=tuple(PROVIDER_DEFAULTS),
            width=11,
            state="readonly",
        )
        provider.grid(row=1, column=column, padx=(0, 8), sticky="w")
        provider.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._provider_changed(provider_var, model_var),
        )
        ttk.Entry(parent, textvariable=model_var, width=25).grid(row=1, column=column + 1, padx=(0, 18), sticky="ew")

    def _output_tab(self, title: str) -> scrolledtext.ScrolledText:
        frame = ttk.Frame(self.tabs, style="Panel.TFrame")
        self.tabs.add(frame, text=title)
        widget = scrolledtext.ScrolledText(
            frame,
            wrap="word",
            bg=PANEL_ALT,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            padx=14,
            pady=14,
            font=("Monospace", 10),
        )
        widget.pack(fill="both", expand=True)
        return widget

    def _provider_changed(self, provider_var: tk.StringVar, model_var: tk.StringVar) -> None:
        model_var.set(PROVIDER_DEFAULTS[provider_var.get()])
        self._refresh_provider_state()

    def _refresh_provider_state(self) -> None:
        states = []
        for provider in dict.fromkeys((self.provider_a.get(), self.provider_b.get())):
            states.append(f"{provider}: {provider_status(provider)}")
        self.key_state.configure(text=" · ".join(states))

    def _set_output(self, widget: scrolledtext.ScrolledText, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _set_status(self, message: str) -> None:
        self.after(0, self.status.set, message)

    def _run(self) -> None:
        task = self.prompt.get("1.0", "end").strip()
        if not task:
            messagebox.showwarning("X-SIDECHAIN", "Enter a task or security claim first.")
            return
        config = replace(
            self.config_data,
            agent_a_provider=self.provider_a.get(),
            agent_a_model=self.model_a.get().strip(),
            agent_b_provider=self.provider_b.get(),
            agent_b_model=self.model_b.get().strip(),
            synthesizer=self.synthesizer.get(),
        )
        if not config.agent_a_model or not config.agent_b_model:
            messagebox.showwarning("X-SIDECHAIN", "Both model names are required.")
            return
        self.store.save(config)
        self.config_data = config
        self.run_button.configure(state="disabled")
        self.progress.start(10)
        self._set_status("Starting")
        threading.Thread(target=self._run_worker, args=(task, config), daemon=True).start()

    def _run_worker(self, task: str, config: AppConfig) -> None:
        try:
            provider_a = create_provider(
                config.agent_a_provider,
                config.agent_a_model,
                config.request_timeout_seconds,
            )
            provider_b = create_provider(
                config.agent_b_provider,
                config.agent_b_model,
                config.request_timeout_seconds,
            )
            result = SidechainOrchestrator(
                provider_a=provider_a,
                provider_b=provider_b,
                agent_a=AgentSpec("Agent A", config.agent_a_provider, config.agent_a_model, EXPLORER_ROLE),
                agent_b=AgentSpec("Agent B", config.agent_b_provider, config.agent_b_model, VALIDATOR_ROLE),
                synthesizer=config.synthesizer,
                progress=self._set_status,
            ).run(task)
        except Exception as exc:  # UI boundary: surface provider and configuration failures.
            self.after(0, self._finish_error, str(exc))
            return
        self.after(0, self._finish_success, result)

    def _finish_success(self, result: DebateResult) -> None:
        self.progress.stop()
        self.run_button.configure(state="normal")
        self._set_output(self.final_output, result.synthesis.text)
        self._set_output(self.transcript_output, _transcript(result))
        self._set_output(
            self.audit_output,
            f"Session: {result.session_id}\nAudit log: {result.audit_path}\n\n"
            f"Verify with:\n  x-sidechain --verify {result.audit_path}\n",
        )
        self.tabs.select(0)
        self.status.set("Completed — evidence log written")

    def _finish_error(self, message: str) -> None:
        self.progress.stop()
        self.run_button.configure(state="normal")
        self.status.set("Failed")
        messagebox.showerror("X-SIDECHAIN", message)


def launch() -> None:
    SidechainApp().mainloop()

