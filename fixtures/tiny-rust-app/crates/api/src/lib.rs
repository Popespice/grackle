pub mod handlers;

use models::{Store, User, UserId, UserStore};

pub struct UserRepository {
    users: Vec<User>,
}

impl UserRepository {
    pub fn new() -> Self {
        UserRepository { users: Vec::new() }
    }

    pub fn add(&mut self, user: User) {
        self.users.push(user);
    }
}

impl Default for UserRepository {
    fn default() -> Self {
        Self::new()
    }
}

impl Store for UserRepository {
    fn find_by_id(&self, id: UserId) -> Option<User> {
        self.users.iter().find(|u| u.id == id).cloned()
    }

    fn count(&self) -> usize {
        self.users.len()
    }
}

impl UserStore for UserRepository {
    fn create(&self, name: String) -> User {
        User::new(self.users.len() as UserId, name)
    }

    fn list(&self) -> Vec<User> {
        self.users.clone()
    }

    fn delete(&self, id: UserId) -> bool {
        self.users.iter().any(|u| u.id == id)
    }
}

pub fn create_user_in_repo(repo: &mut UserRepository, name: String) -> User {
    let user = repo.create(name);
    repo.add(user.clone());
    user
}
