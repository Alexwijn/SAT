from ..manufacturer import Manufacturer


class Worcester(Manufacturer):
    @property
    def friendly_name(self) -> str:
        return 'Worcester Bosch'
