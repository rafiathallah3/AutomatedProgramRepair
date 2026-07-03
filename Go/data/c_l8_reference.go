package main

import "fmt"

func main() {
	var alas, tinggi int
	var luas, keliling int
	fmt.Scan(&alas)
	fmt.Scan(&tinggi)

	luas = alas * tinggi
	keliling = (alas + tinggi) * 2

	fmt.Println(luas)
	fmt.Println(keliling)
}
