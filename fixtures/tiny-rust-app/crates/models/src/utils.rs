use crate::User;

pub fn format_user(user: &User) -> String {
    format!("{}: {}", user.id, user.name)
}

pub fn user_display_name(user: &User) -> String {
    format_user(user)
}

pub fn is_valid_name(name: &str) -> bool {
    !name.is_empty() && name.len() <= 100
}
