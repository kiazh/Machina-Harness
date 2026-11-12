//! Machina Desktop, rebuilt on Rune with OpenCode's design language.

use rune::{
    AppSpec, Color, Cx, El, Icon, ScrollSpec, TextEdit, TextStyle, Tx, col, el, icon, row, text,
};
use rune_kit::{
    IconButton, Kit, Markdown, PaneState, Sidebar, SidebarRow, SidebarSection, SplitView, TextArea,
    Toolbar, shell::SidebarToggle,
};
use rune_theme_opencode::{self as oc, icons};

#[derive(Clone, Copy, PartialEq, Eq)]
enum Role {
    User,
    Assistant,
}

struct Message {
    role: Role,
    body: String,
    time: &'static str,
}

struct State {
    sidebar: PaneState,
    inspector: PaneState,
    selected_session: usize,
    dark: bool,
    inspector_tab: usize,
    draft: TextEdit,
    messages: Vec<Message>,
}

const SESSIONS: [(&str, &str, &str); 6] = [
    ("Native UI migration", "Rune + OpenCode theme", "now"),
    ("Release cleanup", "Remove caches, verify links", "18m"),
    ("Clean benchmark ledger", "Same model, same policy", "1h"),
    ("Tool search latency", "Skip dependency trees", "3h"),
    ("Repair controller", "Caps, phases, evidence", "yesterday"),
    ("Hermes token accounting", "Usage capture", "yesterday"),
];

fn init() -> State {
    State {
        sidebar: PaneState::new(270.0).range(236.0, 360.0),
        inspector: PaneState::new(318.0).range(270.0, 430.0),
        selected_session: 0,
        dark: true,
        inspector_tab: 0,
        draft: TextEdit::new(""),
        messages: vec![
            Message {
                role: Role::User,
                body: "Rebuild the desktop shell on Rune. I want the real workspace, not a mockup: sessions, chat, composer, and the file/changes inspector.".to_owned(),
                time: "10:42",
            },
            Message {
                role: Role::Assistant,
                body: "I’m replacing the React/Tauri shell with Rune’s native renderer and OpenCode’s design language.\n\nThe first pass keeps the interaction model intact:\n\n- resizable session sidebar and workspace inspector\n- a focused chat timeline with tool activity\n- a real multiline composer with submit-on-send\n- light and dark appearance\n\nI’m keeping the backend boundary explicit so the gateway can be wired in after the shell is stable."
                    .to_owned(),
                time: "10:43",
            },
        ],
    }
}

fn avatar(label: &str, color: Color, ink: Color) -> El {
    el().size(26.0, 26.0)
        .radius(8.0)
        .fill(color)
        .center()
        .child(text(label).font(TextStyle::new(12.0, 650.0)).color(ink))
}

fn pill(k: &Kit, label: &str, accent: bool) -> El {
    let c = &k.c;
    text(label)
        .font(k.ty.small_medium)
        .color(if accent { c.text_inverse } else { c.text_muted })
        .px(8.0)
        .py(4.0)
        .radius(999.0)
        .fill(if accent { c.bg_accent } else { c.layer_02 })
        .border(
            1.0,
            if accent {
                Color::TRANSPARENT
            } else {
                c.border_muted
            },
        )
}

fn header_icon(k: &Kit, icon: &'static Icon, label: &str) -> El {
    IconButton::new(icon).label(label).help(label).build(k)
}

fn view(s: &State, cx: &mut Cx<State>) -> El {
    let dark = s.dark;
    let k = oc::kit(dark);
    let c = &k.c;
    let hair = 1.0 / cx.env().scale;

    let brand = row()
        .key("brand")
        .h(58.0)
        .w_full()
        .px(14.0)
        .gap(10.0)
        .center_y()
        .child(
            el().key("brand-mark")
                .size(27.0, 27.0)
                .radius(8.0)
                .fill(oc::brand(dark))
                .center()
                .shadow(1.0, 5.0, Color::BLACK.alpha(0.2))
                .child(
                    text("M")
                        .font(TextStyle::new(13.0, 750.0))
                        .color(Color::WHITE),
                ),
        )
        .child(
            col()
                .key("brand-copy")
                .gap(1.0)
                .child(
                    text("MACHINA")
                        .font(TextStyle::new(12.0, 700.0).tracking(1.1))
                        .color(c.text_base),
                )
                .child(
                    text("local harness")
                        .font(TextStyle::new(10.5, 450.0))
                        .color(c.text_faint),
                ),
        )
        .child(el().grow(1.0).a11y_hidden())
        .child(header_icon(k, &icons::PLUS, "New session"));

    let sessions = SESSIONS
        .iter()
        .enumerate()
        .map(|(i, (title, subtitle, age))| {
            SidebarRow::new(*title)
                .subtitle(*subtitle)
                .height(47.0)
                .detail(*age)
                .selected(i == s.selected_session)
                .on_select(move |s: &mut State, _| s.selected_session = i)
                .build(k)
                .key(format!("session-{i}"))
        })
        .collect::<Vec<_>>();

    let sidebar = Sidebar::new()
        .header(brand)
        .header_gap(12.0)
        .header_padding(0.0, 8.0)
        .section(
            SidebarSection::new("Sessions")
                .count(SESSIONS.len())
                .rows(sessions),
        )
        .footer(
            col()
                .key("account")
                .w_full()
                .gap(8.0)
                .child(el().h(hair).w_full().fill(c.border_muted).a11y_hidden())
                .child(
                    row()
                        .key("account-row")
                        .w_full()
                        .h(40.0)
                        .px(8.0)
                        .gap(9.0)
                        .center_y()
                        .radius(k.radius.md)
                        .hover(c.hover)
                        .on_press(|s: &mut State| s.dark = !s.dark)
                        .child(avatar("KJ", c.bg_inverse, c.text_inverse))
                        .child(
                            col()
                                .gap(0.0)
                                .child(text("Kia").font(k.ty.ui_medium).color(c.text_base))
                                .child(
                                    text("DeepSeek V4.1 Flash")
                                        .font(k.ty.small)
                                        .color(c.text_faint),
                                ),
                        )
                        .child(el().grow(1.0).a11y_hidden())
                        .child(icon(&icons::LAYOUT_RIGHT, 14.0).color(c.icon_muted)),
                ),
        )
        .build(k);

    let main = chat(s, k, hair);

    let toolbar = Toolbar::new()
        .leading(
            row()
                .key("where")
                .gap(7.0)
                .center_y()
                .child(icon(&icons::FOLDER, 14.0).color(c.icon_muted))
                .child(
                    text("C:\\Users\\kiash\\machina")
                        .font(k.ty.small)
                        .color(c.text_faint),
                )
                .child(icon(&icons::BRANCH, 14.0).color(c.icon_muted))
                .child(text("main").font(k.ty.small).color(c.text_muted)),
        )
        .trailing(header_icon(k, &icons::MAGNIFYING_GLASS, "Search sessions"))
        .trailing(
            IconButton::new(&icons::LAYOUT_RIGHT)
                .label("Toggle workspace")
                .help("Toggle workspace")
                .on_press(|s: &mut State| s.inspector.toggle())
                .build(k),
        )
        .trailing(header_icon(k, &icons::SETTINGS_GEAR, "Settings"));

    let shell = SplitView::new(main)
        .sidebar(&s.sidebar, |s: &mut State| &mut s.sidebar, sidebar)
        .inspector(
            &s.inspector,
            |s: &mut State| &mut s.inspector,
            inspector(s, k),
        )
        .toolbar(toolbar)
        .titlebar_inset(cx.titlebar_inset())
        .frame_inset(0.0)
        .toolbar_in_panel()
        .sidebar_padding(10.0, 10.0)
        .sidebar_toggle(
            SidebarToggle::new()
                .size(rune_kit::ControlSize::Large)
                .past_lights(14.0)
                .edge(14.0)
                .in_header(),
        )
        .reduced_motion(cx.is_reduced_motion())
        .build(k);

    rune_kit::root(shell, std::iter::empty::<El>())
}

fn chat(s: &State, k: &Kit, hair: f32) -> El {
    let c = &k.c;
    let timeline = s
        .messages
        .iter()
        .enumerate()
        .map(|(i, m)| match m.role {
            Role::User => user_message(k, m, i),
            Role::Assistant => assistant_message(k, m, i),
        })
        .collect::<Vec<_>>();

    col()
        .key("main")
        .w_full()
        .h_full()
        .fill(c.page)
        .child(
            row()
                .key("session-head")
                .w_full()
                .h(56.0)
                .px(26.0)
                .gap(10.0)
                .center_y()
                .child(
                    col()
                        .gap(2.0)
                        .child(
                            row()
                                .gap(8.0)
                                .center_y()
                                .child(
                                    text(SESSIONS[s.selected_session].0)
                                        .font(k.ty.ui_medium)
                                        .color(c.text_base),
                                )
                                .child(pill(k, "LOCAL", true)),
                        )
                        .child(
                            text("Rune rebuild · visual shell + chat/workspace")
                                .font(k.ty.small)
                                .color(c.text_faint),
                        ),
                )
                .child(el().grow(1.0).a11y_hidden())
                .child(
                    text("Ctrl K")
                        .font(k.ty.small)
                        .color(c.text_faint)
                        .px(8.0)
                        .py(4.0)
                        .radius(k.radius.sm)
                        .fill(c.layer_02)
                        .border(hair, c.border_muted),
                ),
        )
        .child(el().h(hair).w_full().fill(c.border_muted).a11y_hidden())
        .child(
            col()
                .key("chat-scroll")
                .grow(1.0)
                .min_h(0.0)
                .w_full()
                .scroll(ScrollSpec { stick_bottom: false })
                .scroll_edge_fade(18.0)
                .child(
                    col()
                        .key("timeline")
                        .w_full()
                        .px(36.0)
                        .pt(32.0)
                        .pb(20.0)
                        .gap(24.0)
                        .child(
                            col()
                                .key("welcome")
                                .w_full()
                                .gap(7.0)
                                .child(
                                    text("A native cockpit for your agent.")
                                        .font(TextStyle::new(24.0, 650.0).lh(30.0))
                                        .color(c.text_base),
                                )
                                .child(
                                    text("One workspace for sessions, tools, diffs, and the truth about what ran.")
                                        .font(TextStyle::new(13.0, 400.0).lh(20.0))
                                        .color(c.text_muted),
                                ),
                        )
                        .child(el().h(hair).w_full().fill(c.border_muted).a11y_hidden())
                        .children(timeline)
                        .child(tool_activity(k))
                        .child(el().h(12.0).a11y_hidden()),
                ),
        )
        .child(el().h(hair).w_full().fill(c.border_muted).a11y_hidden())
        .child(composer(s, k, hair))
}

fn user_message(k: &Kit, m: &Message, i: usize) -> El {
    let c = &k.c;
    row()
        .key(format!("message-{i}"))
        .w_full()
        .gap(12.0)
        .child(avatar("KJ", c.bg_inverse, c.text_inverse))
        .child(
            col()
                .key("body")
                .grow(1.0)
                .min_w(0.0)
                .gap(6.0)
                .child(
                    row()
                        .gap(8.0)
                        .center_y()
                        .child(text("You").font(k.ty.ui_medium).color(c.text_base))
                        .child(text(m.time).font(k.ty.small).color(c.text_faint)),
                )
                .child(
                    text(m.body.clone())
                        .key("copy")
                        .w_full()
                        .wrap()
                        .font(TextStyle::new(13.5, 400.0).lh(21.0))
                        .color(c.text_base),
                ),
        )
}

fn assistant_message(k: &Kit, m: &Message, i: usize) -> El {
    let c = &k.c;
    row()
        .key(format!("message-{i}"))
        .w_full()
        .gap(12.0)
        .child(avatar("M", oc::brand(k.dark), Color::WHITE))
        .child(
            col()
                .key("body")
                .grow(1.0)
                .min_w(0.0)
                .gap(8.0)
                .child(
                    row()
                        .gap(8.0)
                        .center_y()
                        .child(text("Machina").font(k.ty.ui_medium).color(c.text_base))
                        .child(text("assistant").font(k.ty.small).color(c.text_faint))
                        .child(pill(k, "PLAN", false))
                        .child(text(m.time).font(k.ty.small).color(c.text_faint)),
                )
                .child(
                    Markdown::new(m.body.clone())
                        .selectable(true)
                        .build(k)
                        .key("markdown"),
                ),
        )
}

fn tool_activity(k: &Kit) -> El {
    let c = &k.c;
    col()
        .key("activity")
        .w_full()
        .ml(38.0)
        .gap(8.0)
        .child(
            row()
                .key("activity-title")
                .gap(7.0)
                .center_y()
                .child(icon(&icons::TERMINAL, 13.0).color(c.icon_muted))
                .child(text("Activity").font(k.ty.small_medium).color(c.text_muted))
                .child(text("2 checks").font(k.ty.small).color(c.text_faint)),
        )
        .child(
            col()
                .key("activity-card")
                .w_full()
                .radius(k.radius.lg)
                .border(1.0, c.border_muted)
                .fill(c.layer_01)
                .child(
                    row()
                        .key("activity-read")
                        .w_full()
                        .h(42.0)
                        .px(12.0)
                        .gap(9.0)
                        .center_y()
                        .child(icon(&icons::FILE_DOCUMENT, 14.0).color(c.icon_muted))
                        .child(
                            text("Read apps/desktop/src/app/App.tsx")
                                .font(k.ty.small)
                                .color(c.text_muted),
                        )
                        .child(el().grow(1.0).a11y_hidden())
                        .child(icon(&icons::CHECK, 13.0).color(c.success.fg)),
                )
                .child(
                    row()
                        .key("activity-check")
                        .w_full()
                        .h(42.0)
                        .px(12.0)
                        .gap(9.0)
                        .center_y()
                        .fill(c.layer_02)
                        .child(icon(&icons::TERMINAL, 14.0).color(c.icon_muted))
                        .child(
                            text("cargo check -p machina-desktop")
                                .font(TextStyle::new(12.0, 450.0).mono())
                                .color(c.text_muted),
                        )
                        .child(el().grow(1.0).a11y_hidden())
                        .child(pill(k, "READY", false)),
                ),
        )
}

fn composer(s: &State, k: &Kit, hair: f32) -> El {
    let c = &k.c;
    let input = TextArea::new(&s.draft)
        .width(None)
        .placeholder("Ask Machina to build, fix, or explain…")
        .auto_grow(2, 7)
        .on_edit(|s: &mut State| &mut s.draft)
        .on_submit(submit)
        .build(k)
        .key("composer-input")
        .w_full();

    col()
        .key("composer-wrap")
        .w_full()
        .px(28.0)
        .pb(22.0)
        .pt(12.0)
        .child(
            col()
                .key("composer")
                .w_full()
                .padding(10.0)
                .gap(8.0)
                .radius(18.0)
                .fill(c.layer_01)
                .border(hair, c.border_base)
                .shadow(8.0, 30.0, Color::BLACK.alpha(0.10))
                .child(input)
                .child(
                    row()
                        .key("composer-controls")
                        .w_full()
                        .h(28.0)
                        .px(2.0)
                        .gap(7.0)
                        .center_y()
                        .child(pill(k, "Machina", true))
                        .child(pill(k, "deepseek-flash", false))
                        .child(el().grow(1.0).a11y_hidden())
                        .child(
                            text("Ctrl+Enter to send")
                                .font(k.ty.small)
                                .color(c.text_faint),
                        )
                        .child(
                            el().key("send")
                                .size(29.0, 29.0)
                                .radius(15.0)
                                .fill(c.bg_contrast)
                                .center()
                                .hover(c.contrast_hover)
                                .on_press_tx(submit)
                                .child(icon(&icons::ARROW_UP, 14.0).color(c.icon_contrast)),
                        ),
                ),
        )
}

fn submit(s: &mut State, tx: &mut Tx) {
    let body = s.draft.text.trim().to_owned();
    if body.is_empty() {
        return;
    }
    s.messages.push(Message {
        role: Role::User,
        body,
        time: "now",
    });
    s.draft.clear();
    tx.scroll_to_bottom("chat-scroll");
}

fn inspector(s: &State, k: &Kit) -> El {
    let c = &k.c;
    let tab = |label: &'static str, index: usize| {
        let selected = s.inspector_tab == index;
        text(label)
            .font(k.ty.small_medium)
            .color(if selected { c.text_base } else { c.text_faint })
            .px(9.0)
            .py(6.0)
            .radius(k.radius.sm)
            .fill(if selected {
                c.layer_02
            } else {
                Color::TRANSPARENT
            })
            .hover(c.hover)
            .on_press(move |s: &mut State| s.inspector_tab = index)
    };

    col()
        .key("inspector")
        .w_full()
        .h_full()
        .fill(c.page)
        .px(14.0)
        .pt(14.0)
        .pb(14.0)
        .gap(12.0)
        .child(
            row()
                .key("inspector-head")
                .w_full()
                .h(26.0)
                .center_y()
                .gap(8.0)
                .child(text("Workspace").font(k.ty.ui_medium).color(c.text_base))
                .child(el().grow(1.0).a11y_hidden())
                .child(header_icon(k, &icons::PLUS, "Add file")),
        )
        .child(
            row()
                .key("tabs")
                .w_full()
                .gap(2.0)
                .child(tab("Changes", 0))
                .child(tab("Files", 1))
                .child(el().grow(1.0).a11y_hidden())
                .child(pill(k, "3", false)),
        )
        .child(el().h(1.0).w_full().fill(c.border_muted).a11y_hidden())
        .child(
            col()
                .key("changed-files")
                .w_full()
                .gap(1.0)
                .child(file_row(
                    k,
                    "M",
                    "src/app/App.tsx",
                    "upserted",
                    c.success.fg,
                    true,
                ))
                .child(file_row(
                    k,
                    "A",
                    "apps/desktop/Cargo.toml",
                    "new",
                    c.success.fg,
                    false,
                ))
                .child(file_row(
                    k,
                    "M",
                    "ui/package.json",
                    "override",
                    c.warning.fg,
                    false,
                )),
        )
        .child(
            col()
                .key("run-card")
                .w_full()
                .gap(6.0)
                .padding(10.0)
                .radius(k.radius.md)
                .fill(c.layer_01)
                .border(1.0, c.border_muted)
                .child(
                    row()
                        .gap(6.0)
                        .center_y()
                        .child(icon(&icons::CHECK, 12.0).color(c.success.fg))
                        .child(
                            text("cargo check")
                                .font(k.ty.small_medium)
                                .color(c.text_base),
                        )
                        .child(el().grow(1.0).a11y_hidden())
                        .child(text("0.8s").font(k.ty.small).color(c.text_faint)),
                )
                .child(
                    text("Rune app compiles")
                        .font(k.ty.small)
                        .color(c.text_muted),
                ),
        )
        .child(el().grow(1.0).a11y_hidden())
        .child(
            col()
                .key("inspector-footer")
                .w_full()
                .gap(5.0)
                .child(
                    text("WORKSPACE")
                        .font(TextStyle::new(10.0, 650.0).tracking(1.0))
                        .color(c.text_faint),
                )
                .child(
                    row()
                        .gap(6.0)
                        .center_y()
                        .child(icon(&icons::FOLDER, 12.0).color(c.icon_muted))
                        .child(
                            text("C:\\Users\\kiash\\machina")
                                .font(k.ty.small)
                                .color(c.text_muted),
                        ),
                ),
        )
}

fn file_row(
    k: &Kit,
    status: &str,
    path: &str,
    detail: &str,
    status_color: Color,
    selected: bool,
) -> El {
    let c = &k.c;
    row()
        .key(path.to_owned())
        .w_full()
        .h(34.0)
        .px(8.0)
        .gap(8.0)
        .center_y()
        .radius(k.radius.sm)
        .fill(if selected {
            c.layer_02
        } else {
            Color::TRANSPARENT
        })
        .hover(c.hover)
        .child(
            text(status)
                .font(TextStyle::new(11.0, 650.0).mono())
                .color(status_color)
                .w(10.0),
        )
        .child(icon(&icons::FILE_DOCUMENT, 13.0).color(c.icon_muted))
        .child(
            text(path)
                .font(k.ty.small)
                .color(if selected { c.text_base } else { c.text_muted })
                .truncate(),
        )
        .child(el().grow(1.0).a11y_hidden())
        .child(text(detail).font(k.ty.small).color(c.text_faint))
}

fn main() {
    rune::set_app_icon(include_bytes!("../assets/icon.png").to_vec()).expect("Machina app icon");
    rune::run(AppSpec {
        fonts: oc::FONTS,
        families: oc::FAMILIES,
        scenes: &[("light", |s: &mut State| s.dark = false)],
        ..AppSpec::new("Deus Ex Machina", (1320.0, 860.0), init, view)
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use rune::{Env, Runtime};

    fn runtime(dark: bool) -> Runtime<State> {
        let mut state = init();
        state.dark = dark;
        let mut rt = Runtime::new(
            state,
            view,
            (1320.0, 860.0),
            Env {
                system_dark: dark,
                scale: 1.0,
            },
        );
        rt.frame(0.0);
        rt
    }

    #[test]
    fn shell_renders_in_both_appearances() {
        for dark in [true, false] {
            let rt = runtime(dark);
            assert!(rt.find("chat-scroll").is_some());
            assert!(rt.find("composer-input").is_some());
            assert!(rt.find("send").is_some());
        }
    }

    #[test]
    fn send_clears_the_draft_and_appends_a_message() {
        let mut rt = runtime(true);
        rt.state.draft.replace_text("Ship the shell");
        rt.frame(0.0);
        let (x, y) = rt.find("send").expect("send button");
        rt.pointer_down(x, y);
        rt.pointer_up(x, y);
        rt.frame(0.0);
        assert_eq!(rt.state.messages.len(), 3);
        assert!(
            rt.state
                .messages
                .last()
                .is_some_and(|m| matches!(m.role, Role::User))
        );
        assert!(rt.state.draft.text.is_empty());
    }
}
