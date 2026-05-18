package models

// UserID is an alias for the raw identifier type.
type UserID = int

// User embeds BaseEntity and implements Printable.
type User struct {
	BaseEntity
	Name  string
	Email string
}

func NewUser(name, email string) *User {
	return &User{Name: name, Email: email}
}

func (u *User) Print() string {
	return u.Name
}

func (u *User) Describe() string {
	return u.Name + " <" + u.Email + ">"
}
