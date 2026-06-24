mod calc;

fn main() {
    let result = calc::add(2, 3);
    println!("{}", result);
    let greeting = greet("world");
    println!("{}", greeting);
}

fn greet(name: &str) -> String {
    format!("hello, {}!", name)
}
