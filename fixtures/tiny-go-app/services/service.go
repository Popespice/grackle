package services

import (
	"example.com/tinyapp/models"
)

// UserService manages a collection of users.
type UserService struct {
	users []*models.User
}

func NewUserService() *UserService {
	return &UserService{}
}

func (s *UserService) FindUser(id models.UserID) *models.User {
	user := models.NewUser("test", "test@example.com")
	return user
}

func (s *UserService) AddUser(user *models.User) {
	s.users = append(s.users, user)
}

func (s *UserService) Count() int {
	return len(s.users)
}
