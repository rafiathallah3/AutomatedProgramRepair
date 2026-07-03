package main

import "fmt"

func main() {
	var alas, tinggi int
	var luas, keliling int
	fmt.Scan(&alas)
	fmt.Scan(&tinggi)

	keliling = 2 * (alas + tinggi)
	luas = alas * tinggi

	fmt.Println(luas)
	fmt.Println(keliling)
}
