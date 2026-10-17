from jobtology_be.product_roles.qualification_display import (
    describe_qualification,
    display_qualifications,
)


def test_work_learning_standard_names_become_readable_levels() -> None:
    # Given an NCS work-learning code name
    item = describe_qualification('T5A6', 'SW개발_L5_25V2')
    # Then it reads as family + level and is marked as work-learning
    assert (item.display_name, item.kind, item.name) == ('SW개발 5수준', 'WORK_LEARNING', 'SW개발_L5_25V2')


def test_plain_names_are_national_technical_and_missing_names_fall_back_to_code() -> None:
    # Given a national technical qualification and an unnamed one
    national = describe_qualification('C290', ' 정보처리산업기사 ')
    unnamed = describe_qualification('Q2', None)
    # Then the plain name is kept and the unnamed one shows its code
    assert (national.display_name, national.kind) == ('정보처리산업기사', 'NATIONAL_TECHNICAL')
    assert (unnamed.display_name, unnamed.kind) == ('Q2', None)


def test_only_the_newest_standard_per_family_and_level_remains() -> None:
    # Given several versions of the same work-learning family/level plus other qualifications
    pairs = [
        ('6T0B', 'SW아키텍트_L6_ver1.0'), ('T60A', 'SW아키텍트_L6_ver2.0'),
        ('T63A', 'SW아키텍트_L6_ver3.0'), ('T65A', 'SW아키텍트_L6_20V1'),
        ('T6A6', 'SW아키텍트_L6_25V2'), ('T5A6', 'SW개발_L5_25V2'),
        ('T55A', 'SW개발_L5_20V1'), ('T3A6', 'SW개발_L3_25V3'),
        ('E921', '프로그래밍기능사'), ('C290', '정보처리산업기사'), ('Q2', None),
    ]
    # When display qualifications are selected
    result = display_qualifications(pairs)
    # Then national qualifications come first and each family/level keeps its newest code
    assert [(item.code, item.display_name) for item in result] == [
        ('C290', '정보처리산업기사'), ('E921', '프로그래밍기능사'),
        ('T3A6', 'SW개발 3수준'), ('T5A6', 'SW개발 5수준'), ('T6A6', 'SW아키텍트 6수준'),
        ('Q2', 'Q2'),
    ]


def test_legacy_ver_versions_rank_by_number_when_no_year_version_exists() -> None:
    # Given only pre-2020 versions
    result = display_qualifications([('T50C', 'IT시스템운영_L5_ver2.0'),
                                     ('T53C', 'IT시스템운영_L5_ver3.0')])
    # Then the highest ver number remains
    assert [item.code for item in result] == ['T53C']
