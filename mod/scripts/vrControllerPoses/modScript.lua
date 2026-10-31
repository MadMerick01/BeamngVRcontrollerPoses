-- Runs when the mod is activated: load the pistol (and vrHands) and keep both
-- loaded across level changes. They stay idle until VR controller poses arrive.
setExtensionUnloadMode('vrHands', 'manual')
setExtensionUnloadMode('vrPistol', 'manual')
extensions.load('vrPistol')
