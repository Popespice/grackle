package main

import (
	"fmt"

	"example.com/tinyapp/models"
	"example.com/tinyapp/services"
)

func main() {
	svc := services.NewUserService()
	user := models.NewUser("Alice", "alice@example.com")
	svc.AddUser(user)
	fmt.Println(user.Print())
}
