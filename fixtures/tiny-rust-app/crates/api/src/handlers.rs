use crate::UserRepository;
use models::{Store, User, UserStore};

pub fn handle_get_users(repo: &UserRepository) -> Vec<User> {
    repo.list()
}

pub fn handle_get_user(repo: &UserRepository, id: u64) -> Option<User> {
    repo.find_by_id(id)
}

pub fn handle_create_user(repo: &mut UserRepository, name: String) -> User {
    let user = repo.create(name);
    user
}

pub fn handle_count(repo: &UserRepository) -> usize {
    repo.count()
}
