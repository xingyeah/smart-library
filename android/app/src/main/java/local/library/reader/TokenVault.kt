package local.library.reader

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

private const val KEYSTORE_PROVIDER = "AndroidKeyStore"
private const val KEY_ALIAS = "library.lan-token"
private const val CIPHER_TRANSFORMATION = "AES/GCM/NoPadding"
private const val KEY_SIZE_BITS = 256
private const val GCM_TAG_BITS = 128
private const val GCM_IV_BYTES = 12

/** Encrypts the LAN token with a key that never leaves the Android Keystore (the Keychain's role on iOS). */
class TokenVault {
    fun encrypt(plainText: String): String {
        val cipher = Cipher.getInstance(CIPHER_TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, loadOrCreateKey())
        val encrypted = cipher.doFinal(plainText.toByteArray())
        return Base64.encodeToString(cipher.iv + encrypted, Base64.NO_WRAP)
    }

    /** Throws when the stored text is damaged or the key is gone (for example after a restore to another phone). */
    fun decrypt(stored: String): String {
        val bytes = Base64.decode(stored, Base64.NO_WRAP)
        val cipher = Cipher.getInstance(CIPHER_TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, loadOrCreateKey(), GCMParameterSpec(GCM_TAG_BITS, bytes, 0, GCM_IV_BYTES))
        return String(cipher.doFinal(bytes, GCM_IV_BYTES, bytes.size - GCM_IV_BYTES))
    }

    private fun loadOrCreateKey(): SecretKey {
        val keyStore = KeyStore.getInstance(KEYSTORE_PROVIDER).apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE_PROVIDER)
        generator.init(
            KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(KEY_SIZE_BITS)
                .build(),
        )
        return generator.generateKey()
    }
}
