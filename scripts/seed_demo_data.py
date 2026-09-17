"""
Adds a small set of demo products/categories for local testing.

    python scripts/seed_demo_data.py

Deliberately NOT run automatically anywhere (not on startup, not in
migrations) so it never touches a real production database by accident.
Safe to re-run: it skips categories/products that already exist by slug.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from slugify import slugify  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Category, Product  # noqa: E402

CATEGORIES = ["Toys", "Stationery", "School Bags", "Gifts", "Kids Collection", "Other"]

PRODUCTS = [
    {
        "name": "Teddy Bear",
        "category": "Toys",
        "price": 499,
        "original_price": 699,
        "stock": 12,
        "short_description": "Soft and cuddly teddy bear, perfect for gifting.",
        "featured": True,
        "bestseller": True,
    },
    {
        "name": "Colour Pencil Set",
        "category": "Stationery",
        "price": 149,
        "original_price": None,
        "stock": 40,
        "short_description": "24-shade colour pencil set for school and art.",
        "new_arrival": True,
    },
    {
        "name": "Cartoon School Bag",
        "category": "School Bags",
        "price": 899,
        "original_price": 1199,
        "stock": 8,
        "short_description": "Lightweight, durable school bag with fun cartoon prints.",
        "featured": True,
    },
    {
        "name": "Pencil Box",
        "category": "Stationery",
        "price": 129,
        "original_price": None,
        "stock": 25,
        "short_description": "Multi-compartment pencil box for everyday school use.",
    },
    {
        "name": "Kids Water Bottle",
        "category": "Kids Collection",
        "price": 249,
        "original_price": 299,
        "stock": 20,
        "short_description": "Leak-proof, easy-grip water bottle for kids.",
        "bestseller": True,
    },
    {
        "name": "Drawing Book",
        "category": "Stationery",
        "price": 79,
        "original_price": None,
        "stock": 50,
        "short_description": "Thick-paper drawing book, great for sketching and colouring.",
        "new_arrival": True,
    },
    {
        "name": "Mini Gift Set",
        "category": "Gifts",
        "price": 349,
        "original_price": 449,
        "stock": 15,
        "short_description": "A charming assortment of small gifts for any occasion.",
        "featured": True,
        "new_arrival": True,
    },
]


def main():
    db = SessionLocal()
    try:
        categories_by_name = {}
        for idx, name in enumerate(CATEGORIES):
            slug = slugify(name)
            category = db.query(Category).filter(Category.slug == slug).first()
            if category is None:
                category = Category(name=name, slug=slug, sort_order=idx, active=True)
                db.add(category)
                db.flush()
                print(f"Created category: {name}")
            categories_by_name[name] = category

        for item in PRODUCTS:
            slug = slugify(item["name"])
            if db.query(Product).filter(Product.slug == slug).first() is not None:
                print(f"Skipping existing product: {item['name']}")
                continue
            product = Product(
                name=item["name"],
                slug=slug,
                short_description=item["short_description"],
                category_id=categories_by_name[item["category"]].id,
                price=item["price"],
                original_price=item.get("original_price"),
                stock=item["stock"],
                featured=item.get("featured", False),
                bestseller=item.get("bestseller", False),
                new_arrival=item.get("new_arrival", False),
                active=True,
            )
            db.add(product)
            print(f"Created product: {item['name']}")

        db.commit()
        print("Demo data seeded successfully.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
