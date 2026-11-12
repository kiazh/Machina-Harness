//! Machina Desktop: a Rune shell with OpenCode's design language and Nous
//! Hermes' chat-first, flat product philosophy.

use rune::{
    AppSpec, Color, Cx, El, Icon, ScrollSpec, TextEdit, TextStyle, Tx, auto_grow, col, el, icon,
    row, text, text_input,
};
use rune_kit::{
    IconButton, Kit, Markdown, PaneState, Sidebar, SidebarRow, SidebarSection, SplitView, Toolbar,
    shell::SidebarToggle,
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
    ("Hermes token usage", "Usage capture", "yesterday"),
];

fn seed_messages() -> Vec<Message> {
    vec![
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
    ]
}

fn new_chat(s: &mut State) {
    s.selected_session = 0;
    s.draft.clear();
    s.messages = seed_messages();
}

fn init() -> State {
    State {
        sidebar: PaneState::new(252.0).range(228.0, 340.0),
        inspector: PaneState::new(300.0).range(260.0, 420.0),
        selected_session: 0,
        dark: true,
        inspector_tab: 0,
        draft: TextEdit::new(""),
        messages: seed_messages(),
    }
}

fn avatar(label: &str, color: Color, ink: Color) -> El {
    el().size(24.0, 24.0)
        .radius(6.0)
        .fill(color)
        .center()
        .child(text(label).font(TextStyle::new(11.0, 650.0)).color(ink))
}

fn assistant_mark(dark: bool) -> El {
    el().size(20.0, 20.0)
        .radius(6.0)
        .fill(oc::brand(dark))
        .center()
        .child(
            text("M")
                .font(TextStyle::new(9.0, 750.0))
                .color(Color::WHITE),
        )
}

fn status_dot(k: &Kit, label: &str, color: Color) -> El {
    row()
        .gap(5.0)
        .center_y()
        .child(el().size(6.0, 6.0).radius(3.0).fill(color))
        .child(text(label).font(k.ty.small).color(k.c.text_muted))
}

fn section_label(k: &Kit, label: &str) -> El {
    text(label).font(k.ty.small_medium).color(k.c.text_faint)
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
        .h(52.0)
        .w_full()
        .px(12.0)
        .gap(9.0)
        .center_y()
        .child(
            el().key("brand-mark")
                .size(24.0, 24.0)
                .radius(6.0)
                .fill(oc::brand(dark))
                .center()
                .child(
                    text("M")
                        .font(TextStyle::new(12.0, 750.0))
                        .color(Color::WHITE),
                ),
        )
        .child(
            col()
                .key("brand-copy")
                .gap(1.0)
                .child(
                    text("MACHINA")
                        .font(TextStyle::new(11.5, 700.0).tracking(0.9))
                        .color(c.text_base),
                )
                .child(
                    text("local harness")
                        .font(TextStyle::new(10.0, 450.0))
                        .color(c.text_faint),
                ),
        );

    let new_chat_row = SidebarRow::new("New chat")
        .icon(&icons::PLUS)
        .height(34.0)
        .detail("Ctrl N")
        .on_select(|s: &mut State, _| new_chat(s))
        .build(k)
        .key("new-chat");

    let sessions = SESSIONS
        .iter()
        .enumerate()
        .map(|(i, (title, subtitle, age))| {
            SidebarRow::new(*title)
                .subtitle(*subtitle)
                .height(46.0)
                .detail(*age)
                .selected(i == s.selected_session)
                .on_select(move |s: &mut State, _| s.selected_session = i)
                .build(k)
                .key(format!("session-{i}"))
        })
        .collect::<Vec<_>>();

    let sidebar = Sidebar::new()
        .header(brand)
        .header(new_chat_row)
        .header_gap(8.0)
        .header_padding(0.0, 6.0)
        .section(
            SidebarSection::new("Recent")
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
            col()
                .key("chat-scroll")
                .grow(1.0)
                .min_h(0.0)
                .w_full()
                .scroll(ScrollSpec {
                    stick_bottom: false,
                })
                .scroll_edge_fade(16.0)
                .child(
                    col()
                        .key("timeline")
                        .w_full()
                        .px(32.0)
                        .pt(20.0)
                        .pb(16.0)
                        .gap(22.0)
                        .child(
                            col()
                                .key("session-context")
                                .w_full()
                                .gap(4.0)
                                .child(
                                    row()
                                        .gap(9.0)
                                        .center_y()
                                        .child(
                                            text(SESSIONS[s.selected_session].0)
                                                .font(TextStyle::new(20.0, 650.0).lh(26.0))
                                                .color(c.text_base),
                                        )
                                        .child(status_dot(k, "Local", c.success.fg)),
                                )
                                .child(
                                    text("Rune rebuild · visual shell + chat/workspace")
                                        .font(TextStyle::new(12.5, 400.0).lh(18.0))
                                        .color(c.text_muted),
                                ),
                        )
                        .child(el().h(hair).w_full().fill(c.border_muted).a11y_hidden())
                        .children(timeline)
                        .child(tool_activity(k))
                        .child(el().h(10.0).a11y_hidden()),
                ),
        )
        .child(el().h(hair).w_full().fill(c.border_muted).a11y_hidden())
        .child(composer(s, k, hair))
        .child(el().h(hair).w_full().fill(c.border_muted).a11y_hidden())
        .child(status_bar(k))
}

fn user_message(k: &Kit, m: &Message, i: usize) -> El {
    let c = &k.c;
    row()
        .key(format!("message-{i}"))
        .w_full()
        .justify_end()
        .child(
            col()
                .key("body")
                .max_w(620.0)
                .gap(5.0)
                .child(
                    row()
                        .justify_end()
                        .gap(8.0)
                        .center_y()
                        .child(text(m.time).font(k.ty.small).color(c.text_faint))
                        .child(text("You").font(k.ty.ui_medium).color(c.text_base)),
                )
                .child(
                    text(m.body.clone())
                        .key("copy")
                        .w_full()
                        .wrap()
                        .padding(10.0)
                        .radius(10.0)
                        .fill(c.layer_02)
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
        .gap(10.0)
        .child(assistant_mark(k.dark))
        .child(
            col()
                .key("body")
                .grow(1.0)
                .min_w(0.0)
                .gap(7.0)
                .child(
                    row()
                        .gap(8.0)
                        .center_y()
                        .child(text("Machina").font(k.ty.ui_medium).color(c.text_base))
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
        .ml(30.0)
        .gap(0.0)
        .child(
            row()
                .key("activity-title")
                .w_full()
                .h(30.0)
                .gap(7.0)
                .center_y()
                .child(icon(&icons::TERMINAL, 13.0).color(c.icon_muted))
                .child(text("Activity").font(k.ty.small_medium).color(c.text_muted))
                .child(text("2 tools · 1.4s").font(k.ty.small).color(c.text_faint)),
        )
        .child(el().h(1.0).w_full().fill(c.border_muted).a11y_hidden())
        .child(
            row()
                .key("activity-read")
                .w_full()
                .h(34.0)
                .px(2.0)
                .gap(8.0)
                .center_y()
                .child(icon(&icons::FILE_DOCUMENT, 13.0).color(c.icon_muted))
                .child(
                    text("Read apps/desktop/src/app/App.tsx")
                        .font(k.ty.small)
                        .color(c.text_muted),
                )
                .child(el().grow(1.0).a11y_hidden())
                .child(icon(&icons::CHECK, 12.0).color(c.success.fg))
                .child(text("0.2s").font(k.ty.small).color(c.text_faint)),
        )
        .child(
            row()
                .key("activity-check")
                .w_full()
                .h(34.0)
                .px(2.0)
                .gap(8.0)
                .center_y()
                .child(icon(&icons::TERMINAL, 13.0).color(c.icon_muted))
                .child(
                    text("cargo check -p machina-desktop")
                        .font(TextStyle::new(12.0, 450.0).mono())
                        .color(c.text_muted),
                )
                .child(el().grow(1.0).a11y_hidden())
                .child(text("passed").font(k.ty.small).color(c.success.fg))
                .child(text("0.8s").font(k.ty.small).color(c.text_faint)),
        )
}

fn composer(s: &State, k: &Kit, hair: f32) -> El {
    let c = &k.c;
    let style = TextStyle {
        line_height: 21.0,
        ..k.ty.ui
    };
    let field = text_input(&s.draft, style)
        .w_full()
        .color(c.text_base)
        .placeholder("Message Machina…", c.text_faint)
        .caret(c.text_base, k.selection())
        .on_edit(|s: &mut State| &mut s.draft)
        .on_submit(submit);
    let input = col()
        .key("composer-input")
        .w_full()
        .min_h(50.0)
        .padding(10.0)
        .child(auto_grow(field, 2, 6));

    col()
        .key("composer-wrap")
        .w_full()
        .px(32.0)
        .pb(10.0)
        .pt(10.0)
        .child(
            col()
                .key("composer")
                .w_full()
                .radius(12.0)
                .fill(c.layer_01)
                .border(hair, c.border_muted)
                .child(input)
                .child(
                    row()
                        .key("composer-controls")
                        .w_full()
                        .h(32.0)
                        .px(10.0)
                        .pb(4.0)
                        .gap(8.0)
                        .center_y()
                        .child(text("Machina").font(k.ty.small).color(c.text_muted))
                        .child(text("deepseek-flash").font(k.ty.small).color(c.text_faint))
                        .child(text("Med").font(k.ty.small).color(c.text_faint))
                        .child(el().grow(1.0).a11y_hidden())
                        .child(text("Enter to send").font(k.ty.small).color(c.text_faint))
                        .child(
                            el().key("send")
                                .size(26.0, 26.0)
                                .radius(6.0)
                                .fill(c.bg_contrast)
                                .center()
                                .hover(c.contrast_hover)
                                .on_press_tx(submit)
                                .child(icon(&icons::ARROW_UP, 13.0).color(c.icon_contrast)),
                        ),
                ),
        )
}

fn status_bar(k: &Kit) -> El {
    let c = &k.c;
    row()
        .key("status-bar")
        .w_full()
        .h(28.0)
        .px(32.0)
        .gap(14.0)
        .center_y()
        .child(status_dot(k, "Local", c.success.fg))
        .child(text("main").font(k.ty.small).color(c.text_faint))
        .child(text("deepseek-flash").font(k.ty.small).color(c.text_faint))
        .child(text("Context 12%").font(k.ty.small).color(c.text_faint))
        .child(
            el().size(36.0, 3.0)
                .radius(1.5)
                .fill(c.border_muted)
                .child(el().w(4.5).h(3.0).radius(1.5).fill(oc::brand(k.dark))),
        )
        .child(text("Approvals · Ask").font(k.ty.small).color(c.text_faint))
        .child(el().grow(1.0).a11y_hidden())
        .child(text("Rune 0.1").font(k.ty.small).color(c.text_faint))
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
        col()
            .key(label)
            .gap(5.0)
            .hover(c.hover)
            .on_press(move |s: &mut State| s.inspector_tab = index)
            .child(text(label).font(k.ty.small_medium).color(if selected {
                c.text_base
            } else {
                c.text_faint
            }))
            .child(
                el().h(1.0)
                    .w_full()
                    .fill(if selected {
                        c.text_base
                    } else {
                        Color::TRANSPARENT
                    })
                    .a11y_hidden(),
            )
    };

    col()
        .key("inspector")
        .w_full()
        .h_full()
        .fill(c.page)
        .px(14.0)
        .pt(12.0)
        .pb(12.0)
        .gap(12.0)
        .child(
            row()
                .key("inspector-head")
                .w_full()
                .h(24.0)
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
                .gap(14.0)
                .child(tab("Changes", 0))
                .child(tab("Files", 1))
                .child(el().grow(1.0).a11y_hidden())
                .child(text("3").font(k.ty.small).color(c.text_faint)),
        )
        .child(
            col()
                .key("changed-files")
                .w_full()
                .gap(8.0)
                .child(section_label(k, "Changed files"))
                .child(
                    col()
                        .gap(0.0)
                        .child(file_row(
                            k,
                            "M",
                            "src/app/App.tsx",
                            "+12  -4",
                            c.success.fg,
                            true,
                        ))
                        .child(file_row(
                            k,
                            "A",
                            "apps/desktop/Cargo.toml",
                            "+8",
                            c.success.fg,
                            false,
                        ))
                        .child(file_row(
                            k,
                            "M",
                            "ui/package.json",
                            "+2  -2",
                            c.warning.fg,
                            false,
                        )),
                ),
        )
        .child(
            col()
                .key("checks")
                .w_full()
                .gap(8.0)
                .child(section_label(k, "Checks"))
                .child(
                    row()
                        .w_full()
                        .h(28.0)
                        .gap(7.0)
                        .center_y()
                        .child(icon(&icons::CHECK, 12.0).color(c.success.fg))
                        .child(text("cargo check").font(k.ty.small).color(c.text_muted))
                        .child(el().grow(1.0).a11y_hidden())
                        .child(text("passed · 0.8s").font(k.ty.small).color(c.text_faint)),
                ),
        )
        .child(el().grow(1.0).a11y_hidden())
        .child(
            row()
                .key("inspector-footer")
                .w_full()
                .gap(6.0)
                .center_y()
                .child(icon(&icons::FOLDER, 12.0).color(c.icon_muted))
                .child(
                    text("C:\\Users\\kiash\\machina")
                        .font(k.ty.small)
                        .color(c.text_muted)
                        .truncate(),
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
