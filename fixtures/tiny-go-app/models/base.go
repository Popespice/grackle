package models

// Printable is implemented by any type that can produce a string representation.
type Printable interface {
	Print() string
}

// BaseEntity holds the identity fields common to all persisted models.
type BaseEntity struct {
	ID int
}

func (b *BaseEntity) GetID() int {
	return b.ID
}
