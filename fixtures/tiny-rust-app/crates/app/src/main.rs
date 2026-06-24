use models::Store;

fn main() {
    let mut repo = api::UserRepository::new();
    let user = api::create_user_in_repo(&mut repo, String::from("Alice"));
    println!("{}", user.name);
    let found = repo.find_by_id(user.id);
    println!("found: {}", found.is_some());
    // Note: models::User::deactivate and models::default_user are NOT called here
    // (left cold for runtime trace negative assertions).
}
