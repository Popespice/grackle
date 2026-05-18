pub mod utils;

pub type UserId = u64;

#[derive(Debug, Clone, PartialEq)]
pub enum UserStatus {
    Active,
    Inactive,
    Suspended,
}

/// Base storage trait shared by all repositories.
pub trait Store {
    fn find_by_id(&self, id: UserId) -> Option<User>;
    fn count(&self) -> usize;
}

/// Extended user-specific storage operations. Requires Store.
pub trait UserStore: Store {
    fn create(&self, name: String) -> User;
    fn list(&self) -> Vec<User>;
    fn delete(&self, id: UserId) -> bool;
}

#[derive(Debug, Clone)]
pub struct User {
    pub id: UserId,
    pub name: String,
    pub status: UserStatus,
}

impl User {
    pub fn new(id: UserId, name: String) -> Self {
        User {
            id,
            name,
            status: UserStatus::Active,
        }
    }

    pub fn is_active(&self) -> bool {
        matches!(self.status, UserStatus::Active)
    }

    pub fn deactivate(&mut self) {
        self.status = UserStatus::Inactive;
    }
}

pub fn default_user() -> User {
    User::new(0, String::from("anonymous"))
}
