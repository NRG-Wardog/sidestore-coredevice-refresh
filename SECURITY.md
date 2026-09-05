# Security Policy

Do not report or commit secrets in an issue or pull request. This includes:

- Pairing files
- Host, root, or device private keys
- Certificates or `.p12` files
- Device identifiers
- Signed IPAs containing personal provisioning data
- Full device logs containing personal information

For a suspected credential exposure, remove the material from the working tree,
rotate or revoke the credential, and contact the repository owner privately
before opening a public issue.

This project uses undocumented or platform-private iOS transport behavior.
Use it only on devices and accounts you are authorized to test.
