from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import (
    validate_password,
)
from django.utils import timezone
from django.utils.translation import override

from .models import Evento
from .services.event_configuration import (
    TIMEZONE_DEFAULT,
    VIGENCIAS_ADMINISTRATIVAS,
    inferir_vigencia_meses,
    validar_timezone,
)


TIMEZONE_CHOICES = (
    ("America/Mexico_City", "Centro de México"),
    ("America/Cancun", "Quintana Roo"),
    ("America/Tijuana", "Tijuana"),
    ("America/Hermosillo", "Sonora"),
    ("America/Mazatlan", "Pacífico de México"),
)

VIGENCIA_CHOICES = tuple(
    (months, f"{months} meses") for months in VIGENCIAS_ADMINISTRATIVAS
)


class EventoForm(forms.ModelForm):
    timezone = forms.ChoiceField(
        label="Zona horaria",
        choices=TIMEZONE_CHOICES,
        initial=TIMEZONE_DEFAULT,
    )
    vigencia_meses = forms.TypedChoiceField(
        label="Vigencia administrativa del álbum",
        choices=VIGENCIA_CHOICES,
        initial=VIGENCIAS_ADMINISTRATIVAS[0],
        coerce=int,
    )

    class Meta:
        model = Evento
        fields = [
            "nombre",
            "tipo",
            "fecha",
            "descripcion",
            "mensaje_bienvenida",
        ]

        widgets = {
            "fecha": forms.DateInput(
                attrs={
                    "type": "date",
                }
            ),
            "descripcion": forms.Textarea(
                attrs={
                    "rows": 3,
                }
            ),
            "mensaje_bienvenida": forms.Textarea(
                attrs={
                    "rows": 3,
                }
            ),
        }

    def clean_timezone(self):
        timezone_name = self.cleaned_data["timezone"]
        validar_timezone(timezone_name)
        return timezone_name


class EventoEdicionForm(EventoForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        timezone_initial = self.instance.timezone or TIMEZONE_DEFAULT
        vigencia_initial = inferir_vigencia_meses(self.instance)
        self.fields["timezone"].initial = timezone_initial
        self.fields["vigencia_meses"].initial = vigencia_initial
        self.initial["timezone"] = timezone_initial
        self.initial["vigencia_meses"] = vigencia_initial

        if self.instance.estado in {
            Evento.Estado.ACTIVE,
            Evento.Estado.CLOSED,
        }:
            if not self.instance.timezone:
                self.fields["timezone"].choices = (
                    (("", "No configurada"),) + TIMEZONE_CHOICES
                )
                self.fields["timezone"].required = False
                self.initial["timezone"] = ""
            self.fields["fecha"].disabled = True
            self.fields["timezone"].disabled = True
            self.fields["vigencia_meses"].disabled = True

        if self.instance.estado == Evento.Estado.ARCHIVED:
            for field in self.fields.values():
                field.disabled = True

    def clean_timezone(self):
        timezone_name = self.cleaned_data.get("timezone")
        if not timezone_name and self.instance.estado != Evento.Estado.DRAFT:
            return self.instance.timezone
        return super().clean_timezone()


class AsignarAnfitrionForm(forms.Form):
    usuario = forms.ModelChoiceField(
        label="Usuario existente",
        queryset=User.objects.none(),
        empty_label="Selecciona un usuario",
    )

    def __init__(self, *args, evento, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["usuario"].queryset = User.objects.exclude(
            eventos_asignados=evento
        ).order_by("email", "username")


class EventoTemporalForm(forms.ModelForm):
    fin_planeado = forms.DateTimeField(
        label="Fin planeado del evento",
        required=False,
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
    )
    timezone = forms.CharField(
        label="Zona horaria",
        required=True,
        help_text="Usa una zona IANA, por ejemplo America/Mexico_City.",
    )

    class Meta:
        model = Evento
        fields = ["fin_planeado", "timezone"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        timezone_name = self.instance.timezone or "America/Mexico_City"
        self.fields["timezone"].initial = timezone_name

        if self.instance.fin_planeado:
            try:
                event_timezone = ZoneInfo(timezone_name)
            except ZoneInfoNotFoundError:
                event_timezone = timezone.get_current_timezone()

            self.fields["fin_planeado"].initial = timezone.localtime(
                self.instance.fin_planeado,
                event_timezone,
            )

    def clean_timezone(self):
        timezone_name = self.cleaned_data["timezone"].strip()

        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            raise forms.ValidationError("Ingresa una zona horaria IANA válida.")

        return timezone_name

    def clean(self):
        cleaned_data = super().clean()
        fin_planeado = cleaned_data.get("fin_planeado")
        timezone_name = cleaned_data.get("timezone")

        if self.instance.fin_planeado and fin_planeado is None:
            self.add_error(
                "fin_planeado",
                "El fin planeado no puede vaciarse desde esta configuración.",
            )

        if fin_planeado and timezone_name:
            raw_value = self.data.get(self.add_prefix("fin_planeado"))

            try:
                local_value = datetime.fromisoformat(raw_value)
            except (TypeError, ValueError):
                return cleaned_data

            if local_value.tzinfo is None:
                cleaned_data["fin_planeado"] = local_value.replace(
                    tzinfo=ZoneInfo(timezone_name)
                )

        return cleaned_data

class UsuarioForm(forms.ModelForm):

    ROL_ADMIN = "admin"
    ROL_ANFITRION = "anfitrion"

    rol = forms.ChoiceField(
        choices=[
            (ROL_ADMIN, "Administrador"),
            (ROL_ANFITRION, "Anfitrión"),
        ],
        widget=forms.RadioSelect,
        initial=ROL_ANFITRION,
    )

    eventos = forms.ModelMultipleChoiceField(
        queryset=Evento.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Eventos asignados",
    )

    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "email",
        ]

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()

        if User.objects.filter(username=email).exists():
            raise forms.ValidationError(
                "Ya existe un usuario con este correo."
            )

        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "Ya existe un usuario con este correo."
            )

        return email

    def clean(self):
        cleaned_data = super().clean()

        rol = cleaned_data.get("rol")
        eventos = cleaned_data.get("eventos")

        if rol == self.ROL_ANFITRION and not eventos:
            raise forms.ValidationError(
                "Debes asignar al menos un evento al anfitrión."
            )

        if rol == self.ROL_ADMIN:
            cleaned_data["eventos"] = Evento.objects.none()

        return cleaned_data

    def save(self, commit=True):
        usuario = super().save(commit=False)

        email = self.cleaned_data["email"]

        usuario.username = email
        usuario.email = email

        usuario.set_unusable_password()

        usuario.is_active = False

        if self.cleaned_data["rol"] == self.ROL_ADMIN:
            usuario.is_staff = True
            usuario.is_superuser = True
        else:
            usuario.is_staff = False
            usuario.is_superuser = False

        if commit:
            usuario.save()

        return usuario

class ActivarCuentaForm(forms.Form):

    password = forms.CharField(
        label="Nueva contraseña",
        widget=forms.PasswordInput,
        min_length=8,
    )

    password_confirmacion = forms.CharField(
        label="Confirmar contraseña",
        widget=forms.PasswordInput,
        min_length=8,
    )

    def clean(self):
        cleaned_data = super().clean()

        password = cleaned_data.get("password")
        password_confirmacion = cleaned_data.get(
            "password_confirmacion"
        )

        if password and password_confirmacion:
            if password != password_confirmacion:
                raise forms.ValidationError(
                    "Las contraseñas no coinciden."
                )

        return cleaned_data


class RegistroPublicoForm(forms.Form):
    first_name = forms.CharField(
        label="Nombre",
        max_length=150,
        error_messages={"required": "Este campo es obligatorio."},
    )
    last_name = forms.CharField(
        label="Apellidos",
        max_length=150,
        required=False,
    )
    email = forms.EmailField(
        label="Correo electrónico",
        max_length=150,
        help_text="Usaremos este correo para verificar tu acceso.",
        error_messages={
            "required": "Este campo es obligatorio.",
            "invalid": "Escribe un correo electrónico válido.",
        },
    )
    password = forms.CharField(
        label="Contraseña",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text=(
            "Usa una contraseña difícil de adivinar, con al menos "
            "8 caracteres y que no sea completamente numérica."
        ),
        error_messages={"required": "Este campo es obligatorio."},
    )
    password_confirmacion = forms.CharField(
        label="Confirmar contraseña",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        error_messages={"required": "Este campo es obligatorio."},
    )
    acepta_terminos = forms.BooleanField(
        label="Acepto los Términos de servicio",
        required=True,
        error_messages={"required": "Este campo es obligatorio."},
    )
    acepta_privacidad = forms.BooleanField(
        label="Acepto el Aviso de privacidad",
        required=True,
        error_messages={"required": "Este campo es obligatorio."},
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        self.email_ya_existe = (
            User.objects.filter(username__iexact=email).exists()
            or User.objects.filter(email__iexact=email).exists()
        )
        return email

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        password_confirmacion = cleaned_data.get("password_confirmacion")

        if password and password_confirmacion:
            if password != password_confirmacion:
                self.add_error(
                    "password_confirmacion",
                    "Las contraseñas no coinciden.",
                )
            else:
                usuario = User(
                    username=cleaned_data.get("email", ""),
                    email=cleaned_data.get("email", ""),
                    first_name=cleaned_data.get("first_name", ""),
                    last_name=cleaned_data.get("last_name", ""),
                )
                try:
                    with override("es"):
                        validate_password(password, user=usuario)
                except forms.ValidationError as exc:
                    self.add_error("password", exc)

        return cleaned_data
