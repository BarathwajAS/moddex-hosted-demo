==========================================================
  ModDex  --  see it before you build it
==========================================================

A visualiser for modification / detailing shops. Photograph the
customer's actual bike or car, pick mods, and show them the result on
their own vehicle in about five seconds.


  RUNNING IT
  ----------

  First time only:  open SETUP_ONCE.txt and follow it (5 minutes,
                    browser only). It gives the app its own credentials
                    so there is nothing to log into ever again.

  To launch:        cd C:\ModStudio
                    python ModStudio.py

  To make a single click-and-run .exe:
                    pip install pyinstaller
                    python build_exe.py
                    -> dist\ModDex.exe


  HOW IT WORKS
  ------------

  1  CAPTURE   Four preset angles with an example photo and shooting
               instructions for each.
  2  UPLOAD    Load the customer's photos, or use a demo vehicle.
  3  STUDIO    Pick mods from the sidebar, or just type what the
               customer asks for. Generate.

  Every generated look is cached against a hash of its exact
  configuration. Going back to an earlier look is instant and free --
  it is never regenerated.


  SHOP PRODUCTS  (the button in the top bar)
  ------------------------------------------

  Generic mods look generic. Add your real inventory once -- a product
  photo plus a fitted photo -- and it appears in the catalogue marked
  IN STOCK. Generations then reproduce YOUR actual part, not a
  stock-image approximation. Upload once, reuse forever.


  YOUR DATA
  ---------

  Everything lives in the data\ folder next to the app:
    cache\      generated images
    vehicles\   customer photos
    products\   your product reference photos
    exports\    saved PNGs and contact sheets

  Moving to a new machine? Copy data\ across and the cache and product
  library come with it.


  COSTS
  -----

  Roughly Rs.3.30 per generated image, about Rs.50 per walk-in customer.
  Cached looks cost nothing. Free-trial credits end 24 Oct 2026.
